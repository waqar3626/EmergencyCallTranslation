from pydantic import BaseModel
from datetime import datetime

class ContactRequest(BaseModel):
    phone: str
    email: str
    title: str
    message: str
    address: str

class ContactResponse(BaseModel):
    id: int
    phone: str
    email: str
    title: str
    message: str
    address: str
    date_sent: datetime