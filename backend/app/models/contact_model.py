from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from app.config.database import Base

class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20))
    email = Column(String(100))
    title = Column(String(200))
    message = Column(Text)
    address = Column(String(500))
    date_sent = Column(DateTime, default=func.now())