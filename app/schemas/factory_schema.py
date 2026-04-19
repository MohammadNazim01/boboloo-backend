from pydantic import BaseModel, Field
from typing import List, Optional


class FactoryProvisionRequest(BaseModel):

    factory_device_id: str = Field(
        ...,
        description="Unique device id printed on toy"
    )

    firmware_version: Optional[str] = None
    hardware_revision: Optional[str] = None
    batch_id: Optional[str] = None


class FactoryProvisionResponse(BaseModel):

    toy_uuid: str
    status: str


class FactoryBatchProvisionRequest(BaseModel):

    batch_id: str
    device_ids: List[str]

    firmware_version: Optional[str] = None
    hardware_revision: Optional[str] = None