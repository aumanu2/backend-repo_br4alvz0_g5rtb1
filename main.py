import os
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, timezone
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Design Studio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------- Helpers ---------
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    doc["id"] = str(doc.get("_id"))
    doc.pop("_id", None)
    # convert datetimes
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc

# --------- Models (Requests) ---------
class ProductIn(BaseModel):
    title: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    category: str
    style: Optional[str] = None
    color: Optional[str] = None
    file_types: List[str] = []
    images: List[str] = []
    featured: bool = False
    rating: float = 4.8
    in_stock: bool = True

class OrderItem(BaseModel):
    product_id: str
    title: str
    price: float
    license: str = Field(..., pattern="^(personal|commercial)$")
    quantity: int = Field(1, ge=1)

class CheckoutRequest(BaseModel):
    email: EmailStr
    items: List[OrderItem]
    subtotal: float
    coupon_code: Optional[str] = None
    notes: Optional[str] = None

class CustomRequestIn(BaseModel):
    name: str
    email: EmailStr
    project_type: str
    references: List[str] = []
    colors: Optional[str] = None
    due_date: Optional[str] = None
    budget_estimate: Optional[float] = None
    details: Optional[str] = None

class ProofCommentIn(BaseModel):
    author: str
    message: str
    x: Optional[float] = None
    y: Optional[float] = None

# --------- Public Routes ---------
@app.get("/")
def root():
    return {"message": "Design Studio Backend Running"}

@app.get("/api/products")
def list_products(category: Optional[str] = None, style: Optional[str] = None, color: Optional[str] = None, q: Optional[str] = None, limit: int = 24):
    filter_q: Dict[str, Any] = {}
    if category:
        filter_q["category"] = category
    if style:
        filter_q["style"] = style
    if color:
        filter_q["color"] = color
    if q:
        filter_q["title"] = {"$regex": q, "$options": "i"}
    docs = get_documents("product", filter_q, limit)
    return [serialize_doc(d) for d in docs]

@app.get("/api/products/featured")
def featured_products(limit: int = 8):
    docs = db["product"].find({"featured": True}).limit(limit)
    return [serialize_doc(d) for d in docs]

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        doc = db["product"].find_one({"_id": ObjectId(product_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Product not found")
        return serialize_doc(doc)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid product id")

@app.post("/api/products", status_code=201)
def create_product(payload: ProductIn):
    product = payload.dict()
    _id = create_document("product", product)
    doc = db["product"].find_one({"_id": ObjectId(_id)})
    return serialize_doc(doc)

@app.post("/api/checkout", status_code=201)
def checkout(payload: CheckoutRequest):
    order_doc = {
        "email": payload.email,
        "items": [i.dict() for i in payload.items],
        "subtotal": payload.subtotal,
        "coupon_code": payload.coupon_code,
        "notes": payload.notes,
        "status": "paid",
        "download_links": [f"/downloads/{i.product_id}.zip" for i in payload.items],
        "invoice_url": "/invoices/mock.pdf",
    }
    order_id = create_document("order", order_doc)
    saved = db["order"].find_one({"_id": ObjectId(order_id)})
    return serialize_doc(saved)

@app.post("/api/request-custom", status_code=201)
def request_custom(payload: CustomRequestIn):
    doc = payload.dict()
    doc.update({
        "status": "new",
        "revision_round": 0,
        "project_id": None,
    })
    req_id = create_document("customrequest", doc)
    saved = db["customrequest"].find_one({"_id": ObjectId(req_id)})
    return serialize_doc(saved)

# --------- Designer/Admin/Client Flows ---------
@app.get("/api/projects")
def list_projects(email: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    q: Dict[str, Any] = {}
    if email:
        q["client_email"] = email
    if status:
        q["status"] = status
    docs = db["project"].find(q).limit(limit)
    return [serialize_doc(d) for d in docs]

class ProjectCreateIn(BaseModel):
    title: str
    client_email: EmailStr
    request_id: Optional[str] = None

@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreateIn):
    project = payload.dict()
    project.update({
        "status": "in_progress",
        "drafts": [],
        "comments": [],
        "history": [],
    })
    pid = create_document("project", project)
    saved = db["project"].find_one({"_id": ObjectId(pid)})
    return serialize_doc(saved)

@app.post("/api/projects/{project_id}/upload-draft")
def upload_draft(project_id: str, url: str):
    now = datetime.now(timezone.utc)
    res = db["project"].update_one({"_id": ObjectId(project_id)}, {"$push": {"drafts": {"url": url, "uploaded_at": now}}, "$set": {"updated_at": now}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    doc = db["project"].find_one({"_id": ObjectId(project_id)})
    return serialize_doc(doc)

@app.post("/api/projects/{project_id}/comment")
def add_comment(project_id: str, payload: ProofCommentIn):
    comment = payload.dict()
    comment.update({"created_at": datetime.now(timezone.utc), "status": "open"})
    res = db["project"].update_one({"_id": ObjectId(project_id)}, {"$push": {"comments": comment}, "$set": {"updated_at": datetime.now(timezone.utc)}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    doc = db["project"].find_one({"_id": ObjectId(project_id)})
    return serialize_doc(doc)

@app.post("/api/projects/{project_id}/approve")
def approve_project(project_id: str):
    res = db["project"].update_one({"_id": ObjectId(project_id)}, {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc)}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    doc = db["project"].find_one({"_id": ObjectId(project_id)})
    return serialize_doc(doc)

# --------- Utilities ---------
@app.get("/api/analytics")
def analytics():
    counts = {
        "products": db["product"].count_documents({}),
        "orders": db["order"].count_documents({}),
        "projects": db["project"].count_documents({}),
        "custom_requests": db["customrequest"].count_documents({}),
    }
    top = list(db["product"].find({}).sort("rating", -1).limit(5))
    return {"counts": counts, "top_products": [serialize_doc(d) for d in top]}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
