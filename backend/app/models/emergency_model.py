from sqlalchemy import Column, Integer, String

from app.config.database import Base


class EmergencyLog(Base):

    __tablename__ = "emergency_logs"

    id = Column(Integer, primary_key=True, index=True)

    original_text = Column(String(1000))

    detected_language = Column(String(100))

    translated_text = Column(String(1000))

    emergency_type = Column(String(100))