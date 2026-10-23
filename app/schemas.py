from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal
import re

FieldKind = Literal['participant_name','event_name','event_date','certificate_type','certificate_id','custom_text']
class FieldConfig(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    type: FieldKind
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    alignment: Literal['left','center','right'] = 'center'
    font_family: str = 'Helvetica'
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size: float = Field(default=30, ge=6, le=1000)
    min_font_size: float = Field(default=12, ge=6, le=1000)
    color: str = '#18392F'
    max_width: float = Field(default=.8, gt=0, le=1)
    required: bool = False
    fixed_text: str | None = None
    @field_validator('color')
    @classmethod
    def valid_color(cls, v):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', v): raise ValueError('Color must be a six-digit hex value')
        return v
    @field_validator('font_family')
    @classmethod
    def safe_font_reference(cls,v):
        if len(v)>120 or '/' in v or '\\' in v or v in {'.','..'}: raise ValueError('Font must be a built-in name or a font filename')
        return v
    @model_validator(mode='after')
    def minimum_does_not_exceed_size(self):
        if self.min_font_size > self.font_size: raise ValueError('Minimum font size cannot exceed the configured font size')
        return self

class ConfigUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    page_index: int = Field(default=0, ge=0)
    fields: list[FieldConfig]

class MappingRequest(BaseModel):
    mappings: dict[str, str]
    rows: list[dict]
