---
title: Primitives, Controllers & Skills API
---

# Primitives, Controllers & Skills API

API reference for MCP decorators (`@mcp_tool`, `@mcp_resource`, `@mcp_prompt`), description validation (`validate_tool_descriptions`), class-based `PromptController` and `SkillController` routers, Agent Skills (`SkillSource`, `FilesystemSkillSource`, `Skill`, `SkillFile`, `SkillCatalog`, `SkillIntegrityError`), structured content return types, the `Registry`, and `MCPHandlerService` request context helpers.

For usage guides, see [Marking Routes & Decorators](../learn/marking-routes.md), [Tools & Annotations](../learn/tools.md), [Resources & URI Templates](../learn/resources.md), [Prompts & PromptController](../learn/prompts.md), and [Agent Skills (SEP-2640)](../learn/skills.md).

---

## Route & Function Decorators

::: litestar_mcp.utils.mcp_tool

::: litestar_mcp.utils.mcp_resource

::: litestar_mcp.utils.mcp_prompt

::: litestar_mcp.utils.validate_tool_descriptions

---

## Class-Based Controllers

::: litestar_mcp.mcp.prompt_controller.PromptController

::: litestar_mcp.mcp.prompt_controller.prompt

::: litestar_mcp.mcp.skill_controller.SkillController

---

## Agent Skills (`io.modelcontextprotocol/skills`)

::: litestar_mcp.mcp.skills.SkillSource

::: litestar_mcp.mcp.skills.FilesystemSkillSource

::: litestar_mcp.mcp.skills.Skill

::: litestar_mcp.mcp.skills.SkillFile

::: litestar_mcp.mcp.skills.SkillCatalog

::: litestar_mcp.mcp.skills.SkillIntegrityError

---

## Content Blocks & Return Helpers

::: litestar_mcp.mcp.content.MCPToolResult

::: litestar_mcp.mcp.content.MCPResourceLink

::: litestar_mcp.mcp.content.MCPBlobResource

::: litestar_mcp.mcp.content.MCPInputRequiredResult

::: litestar_mcp.mcp.content.PromptMessage

---

## Discovery Registry

::: litestar_mcp.mcp.registry.Registry

::: litestar_mcp.mcp.registry.ResourceTemplate

::: litestar_mcp.mcp.registry.PromptRegistration

---

## Handler Service & Request Context

::: litestar_mcp.mcp.service.MCPHandlerService

::: litestar_mcp.mcp.service.MCPRequestContext

::: litestar_mcp.mcp.service.get_mcp_request_context
