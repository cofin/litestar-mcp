.. title:: Litestar MCP

.. meta::
   :description: Expose Litestar routes as Model Context Protocol tools, resources, and prompts over Streamable HTTP for AI models.
   :keywords: Litestar, MCP, Model Context Protocol, AI, tools, resources, prompts, JSON-RPC, Streamable HTTP, OpenAPI

.. container:: title-with-logo

   .. raw:: html

      <h1 class="brand-text" aria-label="Litestar MCP">Litestar MCP</h1>

Litestar MCP integrates Litestar web applications with the Model Context
Protocol: mark routes with simple kwargs to expose them as MCP tools,
resources, and prompts that AI models can discover and call over MCP
Streamable HTTP and JSON-RPC — with automatic OpenAPI exposure and optional
bearer-token authentication.

.. toctree::
   :hidden:
   :titlesonly:
   :caption: Documentation

   getting-started
   usage/index
   reference/index

.. toctree::
   :hidden:
   :titlesonly:
   :caption: Development

   contribution-guide
   changelog

.. grid:: 1 1 2 2
   :padding: 0
   :gutter: 2

   .. grid-item-card:: Get Started
      :link: getting-started
      :link-type: doc

      Install Litestar MCP, add the plugin, and mark your first route as an
      MCP tool or resource in a few lines.

   .. grid-item-card:: Usage Guides
      :link: usage/index
      :link-type: doc

      Configure the plugin, mark routes, expose prompts, wire authentication,
      and deploy across replicas.

   .. grid-item-card:: Marking Routes
      :link: usage/marking_routes
      :link-type: doc

      Expose handlers as tools, resources, and prompts with ``mcp_tool`` /
      ``mcp_resource`` / ``mcp_prompt`` kwargs or the dedicated decorators.

   .. grid-item-card:: API Reference
      :link: reference/index
      :link-type: doc

      Browse the generated API reference for the plugin, configuration,
      handlers, and types.

   .. grid-item-card:: Contributing
      :link: contribution-guide
      :link-type: doc

      Set up the development environment, run the quality gates, and add
      coverage for new features.
