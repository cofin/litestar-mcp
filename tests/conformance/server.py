"""Dedicated MCP 2026-07-28 conformance fixture server."""

import asyncio
import os
from typing import Annotated, Any

from litestar import Litestar, get
from litestar.params import Parameter
from litestar.response import Response

from litestar_mcp import (
    LitestarMCP,
    MCPConfig,
    MCPInputRequiredResult,
    MCPTaskConfig,
    MCPToolResult,
    get_mcp_request_context,
    mcp_prompt,
    mcp_tool,
)
from litestar_mcp.jsonrpc import INTERNAL_ERROR, INVALID_PARAMS, JSONRPCError, JSONRPCErrorException

_PIXEL = b"\x89PNG\r\n\x1a\n"
_JSON_SCHEMA_2020_12 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "$defs": {
        "address": {
            "$anchor": "addressDef",
            "type": "object",
            "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
        }
    },
    "properties": {
        "name": {"type": "string"},
        "address": {"$ref": "#/$defs/address"},
        "contactMethod": {"type": "string", "enum": ["phone", "email"]},
        "phone": {"type": "string"},
        "email": {"type": "string"},
    },
    "allOf": [{"anyOf": [{"required": ["phone"]}, {"required": ["email"]}]}],
    "if": {"properties": {"contactMethod": {"const": "phone"}}, "required": ["contactMethod"]},
    "then": {"required": ["phone"]},
    "else": {"required": ["email"]},
    "additionalProperties": False,
}


@get("/conformance/greet", mcp_tool="greet", sync_to_thread=False)
def greet(name: str) -> str:
    return f"Hello, {name}!"


@get("/conformance/text", mcp_tool="test_simple_text", sync_to_thread=False)
def simple_text() -> str:
    return "This is a simple text response for testing."


@get("/conformance/image", mcp_tool="test_image_content", sync_to_thread=False)
def image_content() -> MCPToolResult:
    return MCPToolResult(content={"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"})


@get("/conformance/audio", mcp_tool="test_audio_content", sync_to_thread=False)
def audio_content() -> MCPToolResult:
    return MCPToolResult(content={"type": "audio", "data": "UklGRg==", "mimeType": "audio/wav"})


@get("/conformance/embedded", mcp_tool="test_embedded_resource", sync_to_thread=False)
def embedded_resource() -> MCPToolResult:
    return MCPToolResult(
        content={
            "type": "resource",
            "resource": {
                "uri": "test://embedded-resource",
                "mimeType": "text/plain",
                "text": "This is an embedded resource content.",
            },
        }
    )


@get("/conformance/mixed", mcp_tool="test_multiple_content_types", sync_to_thread=False)
def mixed_content() -> MCPToolResult:
    return MCPToolResult(
        content=[
            {"type": "text", "text": "Multiple content types test:"},
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
            {
                "type": "resource",
                "resource": {
                    "uri": "test://mixed-content-resource",
                    "mimeType": "application/json",
                    "text": '{"test":"data","value":123}',
                },
            },
        ]
    )


@get("/conformance/error", mcp_tool="test_error_handling", sync_to_thread=False)
def error_handling() -> None:
    msg = "This tool intentionally returns an error for testing"
    raise RuntimeError(msg)


@get(
    "/conformance/capability",
    mcp_tool="test_missing_capability",
    mcp_required_client_capabilities={"sampling": {}},
    sync_to_thread=False,
)
def missing_capability() -> str:
    return "Success"


@get(
    "/conformance/header",
    mcp_tool="test_custom_header",
    include_in_schema=False,
    sync_to_thread=False,
)
def custom_header(value: Annotated[str, Parameter(schema_extra={"x-mcp-header": "Value"})]) -> str:
    return value


@get("/conformance/json-schema", sync_to_thread=False)
@mcp_tool(
    name="json_schema_2020_12_tool",
    description="Tool with JSON Schema 2020-12 features",
    input_schema=_JSON_SCHEMA_2020_12,
)
def json_schema_tool() -> str:
    return "JSON Schema input accepted."


@get("/conformance/slow")
@mcp_tool(name="slow_compute", task_support="optional")
async def slow_compute(seconds: float = 0, label: str = "") -> str:
    await asyncio.sleep(seconds)
    return label


@get("/conformance/failing")
@mcp_tool(name="failing_job", task_support="required")
async def failing_job() -> None:
    msg = "intentional task tool error"
    raise RuntimeError(msg)


@get("/conformance/protocol-error")
@mcp_tool(name="protocol_error_job", task_support="required")
async def protocol_error_job() -> None:
    raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message="intentional task protocol error"))


@get("/conformance/confirm")
@mcp_tool(name="confirm_delete", task_support="optional")
async def confirm_delete(filename: str) -> MCPInputRequiredResult | str:
    context = get_mcp_request_context()
    if not context.input_responses:
        return MCPInputRequiredResult(
            input_requests={
                "confirmation": {
                    "method": "elicitation/create",
                    "params": {
                        "message": f"Delete {filename}?",
                        "requestedSchema": {"type": "object", "properties": {"confirm": {"type": "boolean"}}},
                    },
                }
            }
        )
    return filename


@get("/conformance/multi-input")
@mcp_tool(name="multi_input", task_support="required")
async def multi_input() -> MCPInputRequiredResult | str:
    context = get_mcp_request_context()
    if not context.input_responses:
        return MCPInputRequiredResult(
            input_requests={
                "first": {
                    "method": "elicitation/create",
                    "params": {
                        "message": "First value?",
                        "requestedSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
                    },
                },
                "second": {
                    "method": "elicitation/create",
                    "params": {
                        "message": "Second value?",
                        "requestedSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
                    },
                },
            }
        )
    return "Both inputs received."


@get("/conformance/mrtr", mcp_tool="test_input_required_result_elicitation", sync_to_thread=False)
def input_required_elicitation() -> MCPInputRequiredResult | str:
    context = get_mcp_request_context()
    if context.input_responses:
        return "Hello from the completed elicitation."
    return MCPInputRequiredResult(
        input_requests={
            "user_name": {
                "method": "elicitation/create",
                "params": {
                    "message": "What is your name?",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            }
        }
    )


@get("/conformance/mrtr/sampling", mcp_tool="test_input_required_result_sampling", sync_to_thread=False)
def input_required_sampling() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    if context.input_responses:
        return {"sampling": context.input_responses["capital_question"]}
    return MCPInputRequiredResult(
        input_requests={
            "capital_question": {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": "What is the capital of France?"},
                        }
                    ],
                    "maxTokens": 100,
                },
            }
        }
    )


@get("/conformance/mrtr/roots", mcp_tool="test_input_required_result_list_roots", sync_to_thread=False)
def input_required_roots() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    if context.input_responses:
        return {"roots": context.input_responses["client_roots"]}
    return MCPInputRequiredResult(input_requests={"client_roots": {"method": "roots/list", "params": {}}})


@get("/conformance/mrtr/state", mcp_tool="test_input_required_result_request_state", sync_to_thread=False)
def input_required_state() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    state = "conformance-state-ok"
    if context.input_responses:
        if context.request_state != state:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Invalid requestState"))
        return {"message": "state-ok", "input": context.input_responses["confirm"]}
    return MCPInputRequiredResult(
        input_requests={
            "confirm": {
                "method": "elicitation/create",
                "params": {
                    "message": "Please confirm",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"],
                    },
                },
            }
        },
        request_state=state,
    )


@get("/conformance/mrtr/multiple", mcp_tool="test_input_required_result_multiple_inputs", sync_to_thread=False)
def input_required_multiple() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    state = "conformance-multiple-input-state"
    if context.input_responses:
        if context.request_state != state:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Invalid requestState"))
        return {"inputs": context.input_responses}
    return MCPInputRequiredResult(
        input_requests={
            "user_name": {
                "method": "elicitation/create",
                "params": {
                    "message": "What is your name?",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
            "greeting": {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": "Generate a greeting"},
                        }
                    ],
                    "maxTokens": 50,
                },
            },
            "client_roots": {"method": "roots/list", "params": {}},
        },
        request_state=state,
    )


@get("/conformance/mrtr/multi-round", mcp_tool="test_input_required_result_multi_round", sync_to_thread=False)
def input_required_multi_round() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    if context.request_state == "conformance-round-2":
        return {"message": "multi-round complete", "input": context.input_responses}
    if context.request_state == "conformance-round-1":
        return MCPInputRequiredResult(
            input_requests={
                "step2": {
                    "method": "elicitation/create",
                    "params": {
                        "message": "Step 2: What is your favorite color?",
                        "requestedSchema": {
                            "type": "object",
                            "properties": {"color": {"type": "string"}},
                            "required": ["color"],
                        },
                    },
                }
            },
            request_state="conformance-round-2",
        )
    return MCPInputRequiredResult(
        input_requests={
            "step1": {
                "method": "elicitation/create",
                "params": {
                    "message": "Step 1: What is your name?",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            }
        },
        request_state="conformance-round-1",
    )


@get("/conformance/mrtr/tampered", mcp_tool="test_input_required_result_tampered_state", sync_to_thread=False)
def input_required_tampered_state() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    state = "signed-conformance-state"
    if context.input_responses:
        if context.request_state != state:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="requestState integrity failure"))
        return {"message": "state accepted"}
    return MCPInputRequiredResult(
        input_requests={
            "confirm": {
                "method": "elicitation/create",
                "params": {
                    "message": "Confirm the operation",
                    "requestedSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                },
            }
        },
        request_state=state,
    )


@get("/conformance/mrtr/capabilities", mcp_tool="test_input_required_result_capabilities", sync_to_thread=False)
def input_required_capabilities() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    if context.input_responses:
        return {"inputs": context.input_responses}
    capabilities = context.client_capabilities or {}
    requests: dict[str, dict[str, Any]] = {}
    if "sampling" in capabilities:
        requests["sample"] = {
            "method": "sampling/createMessage",
            "params": {
                "messages": [{"role": "user", "content": {"type": "text", "text": "Generate text"}}],
                "maxTokens": 20,
            },
        }
    if "elicitation" in capabilities:
        requests["elicit"] = {
            "method": "elicitation/create",
            "params": {
                "message": "Provide a value",
                "requestedSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
            },
        }
    return MCPInputRequiredResult(input_requests=requests)


@get("/conformance/streaming", mcp_tool="test_streaming_elicitation", sync_to_thread=False)
def streaming_elicitation() -> MCPInputRequiredResult:
    return MCPInputRequiredResult(
        input_requests={
            "confirmation": {
                "method": "elicitation/create",
                "params": {
                    "message": "Continue?",
                    "requestedSchema": {"type": "object", "properties": {"continue": {"type": "boolean"}}},
                },
            }
        }
    )


@get("/conformance/logging", mcp_tool="test_logging_tool", sync_to_thread=False)
def logging_tool() -> str:
    return "No log notification was emitted."


@get("/conformance/trigger-tool-change")
@mcp_tool(name="test_trigger_tool_change")
async def trigger_tool_change() -> str:
    await plugin.registry.notify_tools_list_changed()
    return "Tool list change published."


@get("/conformance/trigger-prompt-change")
@mcp_tool(name="test_trigger_prompt_change")
async def trigger_prompt_change() -> str:
    await plugin.registry.notify_prompts_list_changed()
    return "Prompt list change published."


@get("/conformance/task-with-input")
@mcp_tool(name="test_tool_with_task", task_support="required", task_input_before_start=True)
async def task_with_input() -> MCPInputRequiredResult | dict[str, Any]:
    context = get_mcp_request_context()
    if not context.input_responses:
        return MCPInputRequiredResult(
            input_requests={
                "user_name": {
                    "method": "elicitation/create",
                    "params": {
                        "message": "What is your name?",
                        "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}},
                    },
                }
            }
        )
    return {"message": "Task completed with gathered input.", "user_name": context.input_responses["user_name"]}


@get(
    "/conformance/static-text",
    mcp_resource="static-text",
    mcp_resource_uri="test://static-text",
    mcp_resource_mime_type="text/plain",
    sync_to_thread=False,
)
def static_text() -> str:
    return "This is the content of the static text resource."


@get(
    "/conformance/static-binary",
    mcp_resource="static-binary",
    mcp_resource_uri="test://static-binary",
    mcp_resource_mime_type="image/png",
    sync_to_thread=False,
)
def static_binary() -> Response[bytes]:
    return Response(_PIXEL, media_type="image/png")


@get(
    "/conformance/template/{id:str}",
    mcp_resource="template",
    mcp_resource_template="test://template/{id}/data",
    mcp_resource_mime_type="application/json",
    sync_to_thread=False,
)
def template_resource(id: str) -> dict[str, Any]:  # noqa: A002
    return {"id": id, "templateTest": True, "data": f"Data for ID: {id}"}


@mcp_prompt(name="test_simple_prompt", title="Simple Test Prompt", description="A simple test prompt.")
def simple_prompt() -> str:
    return "This is a simple prompt for testing."


@mcp_prompt(
    name="test_prompt_with_arguments",
    title="Prompt With Arguments",
    description="A parameterized test prompt.",
)
def prompt_with_arguments(arg1: str, arg2: str) -> str:
    return f"Prompt with arguments: arg1='{arg1}', arg2='{arg2}'"


@mcp_prompt(name="test_input_required_result_prompt", description="A prompt requiring client context.")
def input_required_prompt() -> MCPInputRequiredResult | str:
    context = get_mcp_request_context()
    if context.input_responses:
        return "Prompt completed with client context."
    return MCPInputRequiredResult(
        input_requests={
            "user_context": {
                "method": "elicitation/create",
                "params": {
                    "message": "What context should the prompt use?",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"context": {"type": "string"}},
                        "required": ["context"],
                    },
                },
            }
        }
    )


@mcp_prompt(name="test_prompt_with_embedded_resource", description="A prompt with an embedded resource.")
def prompt_with_embedded_resource(resourceUri: str) -> list[dict[str, Any]]:  # noqa: N803
    return [
        {
            "role": "user",
            "content": {
                "type": "resource",
                "resource": {
                    "uri": resourceUri,
                    "mimeType": "text/plain",
                    "text": "Embedded resource content for testing.",
                },
            },
        },
        {
            "role": "user",
            "content": {"type": "text", "text": "Please process the embedded resource above."},
        },
    ]


@mcp_prompt(name="test_prompt_with_image", description="A prompt with image content.")
def prompt_with_image() -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
        },
        {
            "role": "user",
            "content": {"type": "text", "text": "Please analyze the image above."},
        },
    ]


plugin = LitestarMCP(
    MCPConfig(
        name="litestar-mcp-conformance",
        cache_ttl_ms=0,
        cache_scope="private",
        tasks=MCPTaskConfig(default_ttl_ms=300_000, max_ttl_ms=3_600_000, poll_interval_ms=50),
    ),
    prompts=[
        simple_prompt,
        prompt_with_arguments,
        input_required_prompt,
        prompt_with_embedded_resource,
        prompt_with_image,
    ],
)

app = Litestar(
    route_handlers=[
        greet,
        simple_text,
        image_content,
        audio_content,
        embedded_resource,
        mixed_content,
        error_handling,
        missing_capability,
        custom_header,
        json_schema_tool,
        slow_compute,
        failing_job,
        protocol_error_job,
        confirm_delete,
        multi_input,
        input_required_elicitation,
        input_required_sampling,
        input_required_roots,
        input_required_state,
        input_required_multiple,
        input_required_multi_round,
        input_required_tampered_state,
        input_required_capabilities,
        streaming_elicitation,
        logging_tool,
        trigger_tool_change,
        trigger_prompt_change,
        task_with_input,
        static_text,
        static_binary,
        template_resource,
    ],
    plugins=[plugin],
    allowed_hosts=[
        f"127.0.0.1:{os.getenv('MCP_CONFORMANCE_PORT', '18765')}",
        f"localhost:{os.getenv('MCP_CONFORMANCE_PORT', '18765')}",
    ],
)
