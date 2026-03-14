from pydantic import BaseModel
from typing import Optional

class GroupBase(BaseModel):
    name: str

class GroupCreate(GroupBase):
    pass

class Group(GroupBase):
    id: int
    class Config:
        orm_mode = True

class EventBase(BaseModel):
    name: str
    date: str
    location: str
    image_url: Optional[str] = None
    group_id: Optional[int] = None

class EventCreate(EventBase):
    pass

class Event(EventBase):
    id: int
    class Config:
        orm_mode = True

class ParticipantBase(BaseModel):
    name: str
    event_id: int
    group_id: Optional[int] = None

class ParticipantCreate(ParticipantBase):
    pass

class Participant(ParticipantBase):
    id: int
    group_name: Optional[str] = None
    class Config:
        orm_mode = True
