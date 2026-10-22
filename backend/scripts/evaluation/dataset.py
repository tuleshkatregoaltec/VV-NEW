"""Canonical inputs and explicit template binding, independent of application state."""

import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

BENCHMARK = Path(__file__).with_name("benchmark.json")


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^q[0-9]{2,}$")
    title: str = Field(min_length=1)
    question: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameters(self):
        placeholders = list(dict.fromkeys(re.findall(r"\[([^]]+)\]", self.question)))
        if placeholders != self.parameters:
            raise ValueError("parameters must match question placeholders in order")
        return self

    def bind(self, parameters: dict[str, str] | None = None) -> str:
        """No values means the original clarification input; partial bindings are errors."""
        if parameters is None:
            return self.question
        if not isinstance(parameters, dict) or parameters.keys() != set(self.parameters):
            raise ValueError(
                "supply exactly the declared parameters, or leave the template unbound"
            )
        if any(
            not isinstance(value, str) or not value.strip() or "[" in value or "]" in value
            for value in parameters.values()
        ):
            raise ValueError("parameter values must be nonempty concrete values")
        return re.sub(r"\[([^]]+)\]", lambda match: parameters[match[1]], self.question)


class Benchmark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("benchmark case IDs must be unique")
        return self

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


def load_benchmark(path: Path = BENCHMARK) -> Benchmark:
    return Benchmark.model_validate_json(path.read_text())
