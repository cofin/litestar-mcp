"""A2A skill registry and skill registration metadata."""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar_mcp.a2a.types import AgentSkill
from litestar_mcp.shared.introspection import (
    generate_schema_for_handler,
    type_to_json_schema,
)
from litestar_mcp.utils.handler_signature import _parse_docstring_args

if TYPE_CHECKING:
    from litestar.types import Guard


def _extract_docstring_summary(docstring: str | None) -> str | None:
    """Extract summary line from docstring."""
    if not docstring:
        return None
    lines = docstring.strip().splitlines()
    summary_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("Args:", "Returns:", "Raises:", "Example:", "Notes:")):
            break
        summary_lines.append(stripped)
    return " ".join(summary_lines).strip() or None


@dataclass
class SkillRegistration:
    """Registration record for an A2A skill."""

    fn: Callable[..., Any]
    id: str
    name: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    guards: list["Guard"] = field(default_factory=list)

    def to_agent_skill(self) -> AgentSkill:
        """Derive an AgentSkill struct with introspected schemas and docstrings."""
        fn = self.fn
        unwrapped = getattr(fn, "fn", fn)

        doc = getattr(unwrapped, "__doc__", None)
        summary = _extract_docstring_summary(doc)
        desc = self.description or summary or self.name

        in_schema = self.input_schema
        if in_schema is None:
            if hasattr(fn, "create_kwargs_model") or hasattr(fn, "parsed_fn_signature"):
                in_schema = generate_schema_for_handler(fn)  # type: ignore[arg-type]
            else:
                # Introspect plain python callable
                sig = inspect.signature(unwrapped)
                param_docs = _parse_docstring_args(doc)
                properties: dict[str, Any] = {}
                required: list[str] = []
                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "cls", "request"):
                        continue
                    prop_schema = type_to_json_schema(param.annotation)
                    if param_name in param_docs:
                        prop_schema["description"] = param_docs[param_name]
                    properties[param_name] = prop_schema
                    if param.default is inspect.Parameter.empty:
                        required.append(param_name)

                in_schema = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": False,
                }
                if required:
                    in_schema["required"] = required

        out_schema = self.output_schema
        if out_schema is None and hasattr(unwrapped, "__annotations__"):
            ret_type = unwrapped.__annotations__.get("return")
            if ret_type is not None and ret_type is not inspect.Signature.empty:
                out_schema = type_to_json_schema(ret_type)

        return AgentSkill(
            id=self.id,
            name=self.name,
            description=desc,
            input_schema=in_schema or {},
            output_schema=out_schema,
            tags=list(self.tags),
            examples=list(self.examples),
        )


class A2ARegistry:
    """In-memory registry of registered A2A skills."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillRegistration] = {}

    @property
    def skills(self) -> list[SkillRegistration]:
        """List of all registered skills."""
        return list(self._skills.values())

    def register(self, registration: SkillRegistration) -> None:
        """Register a new skill."""
        self._skills[registration.id] = registration

    def get(self, skill_id: str) -> SkillRegistration | None:
        """Retrieve a skill by its unique identifier."""
        return self._skills.get(skill_id)
