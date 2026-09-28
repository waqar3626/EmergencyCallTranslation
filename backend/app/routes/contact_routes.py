from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.config.database import SessionLocal
from app.models.contact_model import ContactMessage
from app.schemas.contact_schema import ContactRequest, ContactResponse

router = APIRouter(
    prefix="/api/contact",
    tags=["Contact"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/", response_model=ContactResponse)
def create_contact_message(contact: ContactRequest, db: Session = Depends(get_db)):
    db_contact = ContactMessage(
        phone=contact.phone,
        email=contact.email,
        title=contact.title,
        message=contact.message,
        address=contact.address
    )
    db.add(db_contact)
    db.commit()
    db.refresh(db_contact)
    return db_contact