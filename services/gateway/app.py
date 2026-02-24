# This file uses fastapi as the API gateway to connect the HTML/CSS pages
# Recieves form submissions
# calls gRPC and then returns an HTML page with the result


from fastapi import FastAPI, Request, Form
# Uses FastAPI: REQUEST: gives access to the HTTP request object, FORM: tells FastAPI to read HTML form fields from POST body
from fastapi.responses import HTMLResponse
# HTMLResponse: tells fastAPI the HTML Response
from fastapi.staticfiles import StaticFiles
# used to serve static CSS files (points to the Folder that has our CSS)
from fastapi.templating import Jinja2Templates
# template for HTML pages in the template folder
from shared.gen import users_pb2, catalog_pb2
# combines users_pb2 & catalog_pb2 
import grpc, grpc_clients

# creates the FastAPI app
app = FastAPI()

# CSS
# - tells FastAPI, any request for styles.css is in the static folder
app.mount("/static", StaticFiles(directory="static"), name="static")

# HTML TEMPLATE
# - the jinja2templates HTML template we are using
templates = Jinja2Templates(directory="templates")

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
        # added the try & except for Error Handling might remove later. -ep
        except grpc.RpcError as e:

    # Renders the users.html page and passes the result to the template.
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "result": result}
    )

# gets the book_id from the books.html section
@app.get("/books", response_class=HTMLResponse)
def books_page(request: Request, book_id: str | None = None):
    result = None
    if book_id:
        res = grpc_clients.catalog_stub().GetBook(catalog_pb2.GetBookRequest(book_id=book_id))
        result = {
            "ok": res.ok,
            "message": res.message,
            "book_id": res.book_id,
            "title": res.title,
            "author": res.author,
        }
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
    res = grpc_clients.users_stub().RegisterUser(
        users_pb2.RegisterUserRequest(name=name, email=email),
        timeout=3,
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "user_id": res.user_id
    }
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
    res = grpc_clients.catalog_stub().PublishBook(
        catalog_pb2.PublishBookRequest(title=title, author=author)
    )
    result = {
        "ok": res.ok,
        "message": res.message,
        "book_id": res.book_id
        }
    # Shows page with result
    return templates.TemplateResponse(
        "books.html",
        {"request": request, "result": result}
        )

# DEBUGGING
# - used to confirm the gateway is running, returns JSON.
# - Optional will remove later -ep
@app.get("/health")
def health():
    return {"ok": True}
