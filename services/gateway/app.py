# This file uses fastapi as the API gateway to connect the HTML/CSS pages
# Recieves form submissions
# calls gRPC and then returns an HTML page with the result


import os
from fastapi import FastAPI, Request, Form
# Uses FastAPI: REQUEST: gives access to the HTTP request object, FORM: tells FastAPI to read HTML form fields from POST body
from fastapi.responses import HTMLResponse
# HTMLResponse: tells fastAPI the HTML Response
from fastapi.staticfiles import StaticFiles
# used to serve static CSS files (points to the Folder that has our CSS)
from fastapi.templating import Jinja2Templates
# template for HTML pages in the template folder
from shared.gen import users_pb2, catalog_pb2, inventory_pb2, circulation_pb2, audit_pb2
from shared.gen import raft_pb2, raft_pb2_grpc
from shared.gen import twopc_pb2
import grpc, grpc_clients

# creates the FastAPI app
app = FastAPI()

# CSS
# - tells FastAPI, any request for styles.css is in the static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

# HTML TEMPLATE
# - the jinja2templates HTML template we are using
templates = Jinja2Templates(directory="templates")
bookIDs = []
logNum = 0

# ── Raft helper stubs ────────────────────────────────────────────────────────

# Static seed nodes used as the starting point for cluster discovery.
# The gateway will call _MEMBERS_ on any reachable seed to learn about
# dynamically-added nodes (e.g. raft-node6 in TC1).
_SEED_NODES = [
    ("raft-node1", "raft-node1:50060"),
    ("raft-node2", "raft-node2:50060"),
    ("raft-node3", "raft-node3:50060"),
    ("raft-node4", "raft-node4:50060"),
    ("raft-node5", "raft-node5:50060"),
]

def _raft_stub(addr: str):
    ch = grpc.insecure_channel(addr)
    return raft_pb2_grpc.RaftServiceStub(ch), ch

def _discover_raft_nodes() -> list[tuple[str, str]]:
    """Ask any reachable seed for the full membership list via _MEMBERS_.
    Falls back to the static seed list if discovery fails."""
    import json
    for _, addr in _SEED_NODES:
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_MEMBERS_"),
                timeout=1,
            )
            ch.close()
            if resp.success:
                members: dict[str, str] = json.loads(resp.result)
                # members is {node_id: "host:port"}
                return sorted(members.items())   # deterministic order
        except Exception:
            pass
    return _SEED_NODES   # fallback

def _get_cluster_status():
    import concurrent.futures as _cf
    nodes = _discover_raft_nodes()
    def probe(node_id, addr):
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_STATUS_"),
                timeout=1,
            )
            ch.close()
            role = resp.result if resp.result in ("leader", "follower", "candidate") else "follower"
            return {"id": node_id, "role": role, "term": "?"}
        except Exception:
            return {"id": node_id, "role": "unknown", "term": "?"}
    with _cf.ThreadPoolExecutor(max_workers=max(len(nodes), 1)) as ex:
        futures = [ex.submit(probe, nid, addr) for nid, addr in nodes]
        return [f.result() for f in futures]

def _get_log_from_leader():
    import json
    for _, addr in _discover_raft_nodes():
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_LOG_"),
                timeout=1,
            )
            ch.close()
            if resp.success:
                data = json.loads(resp.result)
                return data.get("log", []), data.get("commit_index", -1)
        except Exception:
            pass
    return [], -1


def _get_all_node_logs() -> list[dict]:
    """Return per-node log info: [{node_id, log, commit_index, reachable}]."""
    import json, concurrent.futures as _cf

    nodes = _discover_raft_nodes()

    def fetch(node_id, addr):
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_LOG_"),
                timeout=1,
            )
            ch.close()
            if resp.success:
                data = json.loads(resp.result)
                return {
                    "node_id": node_id,
                    "log": data.get("log", []),
                    "commit_index": data.get("commit_index", -1),
                    "reachable": True,
                }
        except Exception:
            pass
        return {"node_id": node_id, "log": [], "commit_index": -1, "reachable": False}

    with _cf.ThreadPoolExecutor(max_workers=max(len(nodes), 1)) as ex:
        futures = [ex.submit(fetch, nid, addr) for nid, addr in nodes]
        return [f.result() for f in futures]

# Page routes ========================================================
# - Get: to read data. (reads HTML response)
# - shows the main index.html page. 
# - TemplateResponse shows the HTML file and returns it to the browser.
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# Get /users
# - Shows the users.html page, also supports user search. With None=None supports if no value is provided.
@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user_id: str | None = None):
    result = None

    # if user_id is in the query string, call the Users gRPC service.
    if user_id:
        try:
            res = grpc_clients.users_stub().GetUser(users_pb2.GetUserRequest(user_id=user_id))
            result = {
                "ok": res.ok,
                "message": res.message,
                "user_id": res.user_id,
                "name": res.name,
                "email": res.email,
            }
        except grpc.RpcError as e:
            result = {
                "ok": False,
                "message": f"gRPC error calling Users: {e.code()} - {e.details()}",
            }

    return templates.TemplateResponse(request, "users.html", {"result": result})


@app.get("/books", response_class=HTMLResponse)
def books_page(request: Request, book_id: str | None = None):
    result = None
    for b in bookIDs:
        res = grpc_clients.catalog_stub().GetBook(catalog_pb2.GetBookRequest(book_id=b))
        result = {
            "ok": res.ok,
            "message": res.message,
            "book_id": res.book_id,
            "title": res.title,
            "author": res.author,
        }

    return templates.TemplateResponse(request, "books.html", {"result": result})


@app.post("/users/register", response_class=HTMLResponse)
def register_user(request: Request, name: str = Form(...), email: str = Form(...)):
    global logNum
    res = grpc_clients.users_stub().RegisterUser(
        users_pb2.RegisterUserRequest(name=name, email=email),
        timeout=10,
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "user_id": res.user_id
    }

    comp = res.user_id + " registered as new user"
    grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1

    return templates.TemplateResponse(request, "users.html", {"result": result})


@app.post("/books/publish", response_class=HTMLResponse)
def publish_book(request: Request, title: str = Form(...), author: str = Form(...)):
    global logNum
    res = grpc_clients.catalog_stub().PublishBook(
        catalog_pb2.PublishBookRequest(title=title, author=author)
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "book_id": res.book_id
    }

    grpc_clients.inventory_stub().AddCopies(
        inventory_pb2.AddCopiesRequest(book_id=res.book_id, count=1), timeout=10
    )
    comp = res.book_id + " published"
    grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))

    bookIDs.append(res.book_id)
    logNum += 1

    return templates.TemplateResponse(request, "books.html", {"result": result})


@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, book_id: str | None = None):
    result = None
    if book_id:
        try:
            res = grpc_clients.inventory_stub().GetAvailability(
                inventory_pb2.BookRequest(book_id=book_id)
            )
            result = {
                "ok": res.ok,
                "message": res.message,
                "book_id": res.book_id,
                "available": res.available
            }
        except grpc.RpcError:
            result = {"ok": False, "message": "gRPC error calling Users"}

    return templates.TemplateResponse(request, "inventory.html", {"result": result})


@app.post("/inventory/add", response_class=HTMLResponse)
def invetory_add(request: Request, book_id: str = Form(...), count: int = Form(...)):
    global logNum
    res = grpc_clients.inventory_stub().AddCopies(
        inventory_pb2.AddCopiesRequest(book_id=book_id, count=count),
        timeout=10,
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "book_id": res.book_id,
        "available": res.available
    }

    comp = f"{count} new copies of {book_id} added"
    grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1

    return templates.TemplateResponse(request, "inventory.html", {"result": result})


@app.get("/circulation", response_class=HTMLResponse)
def circulation_page(request: Request, book_id: str | None = None, user_id: str | None = None):
    result = None
    global logNum

    if book_id and user_id:
        try:
            res1 = grpc_clients.inventory_stub().GetAvailability(
                inventory_pb2.BookRequest(book_id=book_id)
            )
            if res1.available >= 1:
                res = grpc_clients.circulation_stub().CheckoutBook(
                    circulation_pb2.CheckoutRequest(book_id=book_id, user_id=user_id)
                )
                grpc_clients.inventory_stub().DecrementCopy(
                    inventory_pb2.BookRequest(book_id=book_id)
                )
                result = {
                    "ok": res.ok,
                    "message": res.message,
                    "due_date": res.due_date
                }

                comp = f"{book_id} checked out by {user_id}, due on {res.due_date}"
                grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
                logNum += 1

        except grpc.RpcError:
            result = {"ok": False, "message": "gRPC error calling Users"}

    return templates.TemplateResponse(request, "circulation.html", {"result": result})


@app.post("/circulation/checkin", response_class=HTMLResponse)
def circulation_checkout(request: Request, book_id: str = Form(...), user_id: str = Form(...)):
    global logNum

    res = grpc_clients.circulation_stub().CheckinBook(
        circulation_pb2.CheckinRequest(book_id=book_id, user_id=user_id)
    )
    grpc_clients.inventory_stub().AddCopies(
        inventory_pb2.AddCopiesRequest(book_id=book_id, count=1), timeout=10
    )

    result = {
        "ok": res.ok,
        "message": res.message,
    }

    comp = f"{book_id} checked in by {user_id}"
    grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1

    return templates.TemplateResponse(request, "circulation.html", {"result": result})


@app.get("/audit", response_class=HTMLResponse)
def log_page(request: Request, book_id: str | None = None):
    result = None
    global logNum

    for b in range(logNum):
        res = grpc_clients.audit_stub().LogEvent(
            audit_pb2.LogRequest(event_type="2", description=str(b))
        )
        result = {"ok": res.ok, "message": res.message}


    return templates.TemplateResponse(request, "audit.html", {"result": result})

@app.get("/2pc", response_class=HTMLResponse)
def twopc_page(request: Request):
    return templates.TemplateResponse(request, "twopc.html", {"result": None})


@app.post("/2pc/checkout", response_class=HTMLResponse)
def twopc_checkout(request: Request, book_id: str = Form(...), user_id: str = Form(...)):
    res = grpc_clients.twopc_coordinator_stub().BeginTransaction(
        twopc_pb2.BeginRequest(operation="CHECKOUT", book_id=book_id, user_id=user_id)
    )

    result = {
        "transaction_id": res.transaction_id,
        "all_commit": res.all_commit,
        "summary": res.summary,
        "votes": [
            {"participant": v.participant, "committed": v.committed, "reason": v.reason}
            for v in res.votes
        ],
        "acks": [
            {"participant": a.participant, "ok": a.ok, "message": a.message}
            for a in res.acks
        ],
    }

    return templates.TemplateResponse(request, "twopc.html", {"result": result})


@app.get("/raft", response_class=HTMLResponse)
def raft_page(request: Request):
    nodes = _get_cluster_status()
    log_entries, commit_index = _get_log_from_leader()
    node_logs = _get_all_node_logs()

    return templates.TemplateResponse(request, "raft.html", {
        "result": None,
        "operation": "",
        "nodes": nodes,
        "log_entries": log_entries,
        "commit_index": commit_index,
        "node_logs": node_logs,
    })


@app.post("/raft/command", response_class=HTMLResponse)
async def raft_command(request: Request, operation: str = Form(...)):
    result = None
    log_entries, commit_index = [], -1

    primary_addr = os.getenv("RAFT_ADDR", "raft-node1:50060")
    try:
        stub, ch = _raft_stub(primary_addr)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation=operation),
            timeout=12,
        )
        ch.close()

        result = {
            "success": resp.success,
            "result": resp.result,
            "leader_id": resp.leader_id,
        }

        log_entries, commit_index = _get_log_from_leader()

    except grpc.RpcError as e:
        result = {"success": False, "result": f"gRPC error: {e.details()}", "leader_id": ""}

    nodes = _get_cluster_status()
    node_logs = _get_all_node_logs()

    return templates.TemplateResponse(request, "raft.html", {
        "result": result,
        "operation": operation,
        "nodes": nodes,
        "log_entries": log_entries,
        "commit_index": commit_index,
        "node_logs": node_logs,
    })