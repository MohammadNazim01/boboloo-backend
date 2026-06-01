from pydantic import BaseModel, Field
from typing import List, Optional

# Agreed device-ID format: 4-32 uppercase-normalised alphanumeric characters
# plus hyphens.  The routes call .strip().upper() before storage, so lowercase
# is accepted here and normalised at the boundary.
DEVICE_ID_PATTERN = r"^[A-Za-z0-9\-]{4,32}$"


class FactoryProvisionRequest(BaseModel):

    factory_device_id: str = Field(
        ...,
        min_length=4,
        max_length=32,
        pattern=DEVICE_ID_PATTERN,
        description="Unique device ID printed on the toy (4-32 alphanumeric/hyphen chars)",
    )

    firmware_version: Optional[str] = None
    hardware_revision: Optional[str] = None
    batch_id: Optional[str] = None


class FactoryProvisionResponse(BaseModel):

    toy_uuid: str
    status: str


class FactoryBatchProvisionRequest(BaseModel):

    batch_id: str
    device_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Device IDs to provision (1-500 per request)",
    )

    firmware_version: Optional[str] = None
    hardware_revision: Optional[str] = None


class FactoryDisableRequest(BaseModel):

    factory_device_id: str = Field(
        ...,
        min_length=4,
        max_length=32,
        pattern=DEVICE_ID_PATTERN,
        description="Device ID of the toy to disable",
    )
