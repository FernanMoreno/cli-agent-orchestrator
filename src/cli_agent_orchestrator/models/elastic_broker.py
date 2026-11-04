"""Broker replies are data contracts, never implicit resource authority."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)


class ElasticWorkerIdentity(BaseModel):
    # Broker responses may add metadata such as a private callback token. This
    # consumer uses only its declared fields and never persists those extras.
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore", hide_input_in_errors=True)

    worker_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

    @field_validator("worker_id")
    @classmethod
    def canonical_worker(cls, value: str) -> str:
        if value.strip() != value or not value.isprintable():
            raise ValueError("worker identity must be a canonical URL segment")
        return value


class ElasticWorkerLease(ElasticWorkerIdentity):
    target_host: StrictStr = Field(min_length=1, max_length=2048)
    working_directory: StrictStr = Field(min_length=1, max_length=4096)
    session_name: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("target_host", "working_directory", "session_name")
    @classmethod
    def canonical_coordinate(cls, value: str) -> str:
        if value.strip() != value or not value.isprintable():
            raise ValueError("worker coordinates must be canonical and printable")
        return value


class ElasticWorkerRelease(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore", hide_input_in_errors=True)

    released: StrictBool
    workload_present: StrictBool

    @model_validator(mode="after")
    def positive_absence(self) -> "ElasticWorkerRelease":
        if self.released is not True or self.workload_present is not False:
            raise ValueError("broker did not confirm workload absence")
        return self
