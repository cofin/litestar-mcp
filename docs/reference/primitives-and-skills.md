---
title: Primitives, Controllers & Skills API
---

# Primitives, Controllers & Skills API

API reference for MCP decorators (`@mcp_tool`, `@mcp_resource`, `@mcp_prompt`, including `output_schema`, `title`, `icons`, `meta`, and `classify_resource_uri` URI classification), class-based `PromptController` and `SkillController` routers, Agent Skills (`SkillSource`, `FilesystemSkillSource`, `Skill`, `SkillFile` metadata, `filter_skills`, request-optional `SkillCatalog` with `notify_list_changed`, `SkillIntegrityError`), structured content return types, the `Registry` (`notify_resources_list_changed`, `notify_skills_list_changed`), and request context helpers (`MCPRequestContext.log`, `ui_capabilities`, `session_id`, and overloaded `get_mcp_request_context(default=...)`). For `MCPHandlerService`, see [Plugin & Configuration API](plugin-and-config.md); for `validate_tool_descriptions` and internal ASGI execution exceptions, see [Core Primitives & Observability API](core-and-observability.md).

For usage guides, see [Marking Routes & Decorators](../learn/marking-routes.md), [Tools & Annotations](../learn/tools.md), [Resources & URI Templates](../learn/resources.md), [Prompts & PromptController](../learn/prompts.md), and [Agent Skills (SEP-2640)](../learn/skills.md).

## Route & Function Decorators

::: litestar_mcp.utils.mcp_tool

::: litestar_mcp.utils.mcp_resource

::: litestar_mcp.utils.mcp_prompt

::: litestar_mcp.utils.classify_resource_uri

## Class-Based Controllers

::: litestar_mcp.mcp.prompt_controller.PromptController

::: litestar_mcp.mcp.prompt_controller.prompt

::: litestar_mcp.mcp.content.PromptMessage

::: litestar_mcp.mcp.skill_controller.SkillController

## Agent Skills

::: litestar_mcp.mcp.skills.SkillSource

::: litestar_mcp.mcp.skills.FilesystemSkillSource

::: litestar_mcp.mcp.skills.Skill

::: litestar_mcp.mcp.skills.SkillFile

::: litestar_mcp.mcp.skills.SkillCatalog

::: litestar_mcp.mcp.skills.filter_skills

::: litestar_mcp.mcp.skills.SkillIntegrityError

## Content Blocks & Return Helpers

::: litestar_mcp.mcp.content.MCPToolResult

::: litestar_mcp.mcp.content.MCPResourceLink

::: litestar_mcp.mcp.content.MCPBlobResource

::: litestar_mcp.mcp.content.MCPInputRequiredResult

## Discovery Registry

::: litestar_mcp.mcp.registry.Registry

::: litestar_mcp.mcp.registry.ResourceTemplate

::: litestar_mcp.mcp.registry.PromptRegistration

## Request Context

::: litestar_mcp.mcp.service.MCPRequestContext

::: litestar_mcp.mcp.service.get_mcp_request_context
