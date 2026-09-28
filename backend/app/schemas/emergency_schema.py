from pydantic import BaseModel


class EmergencyResponse(BaseModel):

    original_text: str

    detected_language: str

    translated_text: str

    emergency_type: str