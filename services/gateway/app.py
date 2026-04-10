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

# Page routes ========================================================
# - Get: to read data. (reads HTML response)
# - shows the main index.html page. 
# - TemplateResponse shows the HTML file and returns it to the browser.
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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
    # Renders the users.html page and passes the result to the template.
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "result": result},
    )

# gets the book_id from the books.html section
@app.get("/books", response_class=HTMLResponse)
def books_page(request: Request, book_id: str | None = None):
    result = None
    #if book_id:
    for b in bookIDs:
        res = grpc_clients.catalog_stub().GetBook(catalog_pb2.GetBookRequest(book_id=b))
        result = {
            "ok": res.ok,
            "message": res.message,
            "book_id": res.book_id,
            "title": res.title,
            "author": res.author,
        }
        print(res.title +  " " + res.author + " " + res.book_id)
    return templates.TemplateResponse(
        "books.html",
        {"request": request, "result": result}
    )

# Action Rules =================================================
# - Handles HTML form submissisons
# - POST called when the user submits the Register HTML form.
# - Reads name, email. Calls UsersService.RegisterUser from gRPC
# - Re-renders users.html with results msg
@app.post("/users/register", response_class=HTMLResponse)
def register_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...)
    ):
    global logNum
    res = grpc_clients.users_stub().RegisterUser(
        users_pb2.RegisterUserRequest(name=name, email=email),
        timeout=3,
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "user_id": res.user_id
    }
    print("your user id: " + res.user_id)
    comp = res.user_id + " registered as new user"
    res = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1
    # Shows page with results
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "result": result}
    )

# POST /books/publish
# - Is called when user submits the Publish Book HTML form. Reads title & author.
# - Calls CatalogService.PublishBook from gRPC. Shows books.html with result msg.
@app.post("/books/publish", response_class=HTMLResponse)
def publish_book(
    request: Request,
    title: str = Form(...),
    author: str = Form(...)
    ):
    global logNum
    res = grpc_clients.catalog_stub().PublishBook(
        catalog_pb2.PublishBookRequest(title=title, author=author)
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "book_id": res.book_id
        }
    # Shows page with result
    res1 = grpc_clients.inventory_stub().AddCopies(inventory_pb2.AddCopiesRequest(book_id=res.book_id, count=1), timeout=3,)
    comp = res.book_id + " published"
    res2 = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    bookIDs.append(res.book_id)
    logNum += 1
    return templates.TemplateResponse(
        "books.html",
        {"request": request, "result": result}
        )

@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, book_id: str | None = None):
    result = None

    # if user_id is in the query string, call the Users gRPC service.
    if book_id:
        try:
            res = grpc_clients.inventory_stub().GetAvailability(inventory_pb2.BookRequest(book_id=book_id))
            result = {
                "ok": res.ok,
                "message": res.message,
                "book_id": res.book_id,
                "available": res.available
            }
            print(res.book_id + " " + str(res.available))
        # added the try & except for Error Handling might remove later. -ep
        except grpc.RpcError as e:
            result = { "ok": False, "message": f"gRPC error calling Users"}

    # Renders the users.html page and passes the result to the template.
    return templates.TemplateResponse(
        "inventory.html",
        {"request": request, "result": result}
    )

@app.post("/inventory/add", response_class=HTMLResponse)
def invetory_add(
    request: Request,
    book_id: str = Form(...),
    count: int = Form(...)
    ):
    global logNum
    res = grpc_clients.inventory_stub().AddCopies(
        inventory_pb2.AddCopiesRequest(book_id=book_id, count=count),
        timeout=3,
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "book_id": res.book_id,
        "available": res.available
    }
    comp = str(count) + " new copies of " + book_id + " added"
    res = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1
    # Shows page with results
    return templates.TemplateResponse(
        "inventory.html",
        {"request": request, "result": result}
    )

@app.get("/circulation", response_class=HTMLResponse)
def circulation_page(request: Request, book_id: str | None = None, user_id: str | None = None):
    result = None
    global logNum
    # if user_id is in the query string, call the Users gRPC service.
    if book_id and user_id:
        try:
            res1 = grpc_clients.inventory_stub().GetAvailability(inventory_pb2.BookRequest(book_id=book_id))
            if res1.available >= 1:
                res = grpc_clients.circulation_stub().CheckoutBook(circulation_pb2.CheckoutRequest(book_id=book_id, user_id=user_id))
                res2 = grpc_clients.inventory_stub().DecrementCopy(inventory_pb2.BookRequest(book_id=book_id))
                result = {
                    "ok": res.ok,
                    "message": res.message,
                    "due_date": res.due_date
                }
                comp = book_id + " checked out by " + user_id + ", due on " + res.due_date
                res = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
                logNum += 1
                print(comp)
        # added the try & except for Error Handling might remove later. -ep
        except grpc.RpcError as e:
            result = { "ok": False, "message": f"gRPC error calling Users"}

    # Renders the users.html page and passes the result to the template.
    return templates.TemplateResponse(
        "circulation.html",
        {"request": request, "result": result}
    )

@app.post("/circulation/checkin", response_class=HTMLResponse)
def circulation_checkout(request: Request, book_id: str = Form(...) , user_id: str = Form(...)):
    result = None
    global logNum
    # if user_id is in the query string, call the Users gRPC service.

    res = grpc_clients.circulation_stub().CheckinBook(circulation_pb2.CheckinRequest(book_id=book_id, user_id=user_id))
    res2 = grpc_clients.inventory_stub().AddCopies(inventory_pb2.AddCopiesRequest(book_id=book_id, count=1),timeout=3,)
    result = {
        "ok": res.ok,
        "message": res.message,
    }
    comp = book_id + " checked in by " + user_id
    print(comp)
    res1 = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="1", description=comp))
    logNum += 1
# added the try & except for Error Handling might remove later. -ep

    # Renders the users.html page and passes the result to the template.
    return templates.TemplateResponse(
        "circulation.html",
        {"request": request, "result": result}
    )

@app.get("/audit", response_class=HTMLResponse)
def log_page(request: Request, book_id: str | None = None):
    result = None
    global logNum
    for b in range(logNum):
        res = grpc_clients.audit_stub().LogEvent(audit_pb2.LogRequest(event_type="2", description=str(b)))
        result = {
            "ok": res.ok,
            "message": res.message,
        }
        print(res.message)
    return templates.TemplateResponse(
        "audit.html",
        {"request": request, "result": result}
    )


# ── 2PC Vote Phase ────────────────────────────────────────────────────────────

@app.get("/2pc", response_class=HTMLResponse)
def twopc_page(request: Request):
    return templates.TemplateResponse("twopc.html", {"request": request, "result": None})

@app.post("/2pc/checkout", response_class=HTMLResponse)
def twopc_checkout(
    request: Request,
    book_id: str = Form(...),
    user_id: str = Form(...),
):
    res = grpc_clients.twopc_coordinator_stub().BeginTransaction(
        twopc_pb2.BeginRequest(
            operation="CHECKOUT",
            book_id=book_id,
            user_id=user_id,
        )
    )
    result = {
        "transaction_id": res.transaction_id,
        "all_commit": res.all_commit,
        "summary": res.summary,
        "votes": [
            {"participant": v.participant, "committed": v.committed, "reason": v.reason}
            for v in res.votes
        ],
    }
    return templates.TemplateResponse("twopc.html", {"request": request, "result": result})


# DEBUGGING
# - used to confirm the gateway is running, returns JSON.
# - Optional will remove later -ep
@app.get("/health")
def health():
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────────────────
#  Raft Routes  (Q3 + Q4)
# ─────────────────────────────────────────────────────────────────────────────

# Known raft node addresses (mirrors docker-compose PEERS)
RAFT_NODES = [
    ("raft-node1", os.getenv("RAFT_ADDR", "raft-node1:50060")),
    ("raft-node2", "raft-node2:50060"),
    ("raft-node3", "raft-node3:50060"),
    ("raft-node4", "raft-node4:50060"),
    ("raft-node5", "raft-node5:50060"),
]


def _raft_stub(addr: str):
    ch = grpc.insecure_channel(addr)
    return raft_pb2_grpc.RaftServiceStub(ch), ch


def _get_cluster_status():
    """Probe each raft node via _STATUS_ RPC. Returns list of dicts."""
    import concurrent.futures as _cf

    def probe(node_id, addr):
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_STATUS_"),
                timeout=1,
            )
            ch.close()
            # resp.result = state string ("leader"/"follower"/"candidate")
            role = resp.result if resp.result in ("leader", "follower", "candidate") else "follower"
            return {"id": node_id, "role": role, "term": "?"}
        except Exception:
            return {"id": node_id, "role": "unknown", "term": "?"}

    with _cf.ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(probe, nid, addr) for nid, addr in RAFT_NODES]
        return [f.result() for f in futures]


def _get_log_from_leader():
    """Ask each node for its log via a ClientRequest; first success wins."""
    for _, addr in RAFT_NODES:
        try:
            stub, ch = _raft_stub(addr)
            resp = stub.ClientRequest(
                raft_pb2.ClientRequestMessage(operation="_LOG_"),
                timeout=1,
            )
            ch.close()
            if resp.success:
                # Parse: result is JSON-like "idx:term:op|idx:term:op|..."
                import json
                try:
                    data = json.loads(resp.result)
                    return data.get("log", []), data.get("commit_index", -1)
                except Exception:
                    return [], -1
        except Exception:
            pass
    return [], -1


@app.get("/raft", response_class=HTMLResponse)
def raft_page(request: Request):
    nodes = _get_cluster_status()
    log_entries, commit_index = _get_log_from_leader()
    return templates.TemplateResponse("raft.html", {
        "request": request,
        "result": None,
        "operation": "",
        "nodes": nodes,
        "log_entries": log_entries,
        "commit_index": commit_index,
    })


@app.post("/raft/command", response_class=HTMLResponse)
async def raft_command(request: Request, operation: str = Form(...)):
    result = None
    log_entries, commit_index = [], -1

    # Send to the primary raft node (it will forward if not leader)
    primary_addr = os.getenv("RAFT_ADDR", "raft-node1:50060")
    try:
        stub, ch = _raft_stub(primary_addr)
        resp = stub.ClientRequest(
            raft_pb2.ClientRequestMessage(operation=operation),
            timeout=5,
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
    return templates.TemplateResponse("raft.html", {
        "request": request,
        "result": result,
        "operation": operation,
        "nodes": nodes,
        "log_entries": log_entries,
        "commit_index": commit_index,
    })
