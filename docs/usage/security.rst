========
Security
========

MCP exposes another transport into your Litestar application. Treat each
MCP tool, resource, and prompt as the same domain operation that an HTTP
handler represents: authenticate the caller at the boundary, then authorize
the requested object inside your application.

Task Ownership Is Transport Isolation
=====================================

When MCP task support is enabled, litestar-mcp stores each task with an
``owner_id`` and only returns that task to the same owner. This prevents one
MCP client session or principal from reading another client's task state.

That task owner check is transport-level isolation. It does not prove that
the caller may access the domain object named in the tool arguments. Your
application must still validate relationships such as:

- the authenticated user owns the requested ``workspace_id`` before running a
  workspace export;
- the project named in ``project_id`` contains the requested file;
- the caller's tenant is allowed to read or mutate the requested resource.

Use normal Litestar guards, dependencies, middleware, and service filters for
those checks. MCP dispatch routes through the same handler machinery, so
authorization code should not need an MCP-specific branch.

.. literalinclude:: /examples/snippets/domain_authorization.py
    :language: python
    :caption: ``docs/examples/snippets/domain_authorization.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

Identity By Transport
=====================

The value you compare against requested object IDs depends on the transport:

- **Streamable HTTP with auth middleware**: Litestar middleware validates the
  token and populates ``request.user`` and ``request.scope["auth"]``. Compare
  tool arguments with those server-validated values.
- **In-process stdio with MCPStdioContext**:
  :class:`~litestar_mcp.MCPStdioContext` populates the synthetic Litestar
  request scope for local process use. Resolve credentials from the host
  environment, OS profile, or another local source before creating that
  context.
- **Stdio bridge to a remote MCP server**: the local bridge is only a
  transport adapter. It must not invent server-side identity. The remote HTTP
  MCP server still authenticates the caller and supplies the identity used by
  guards and dependencies.

Do not trust client-supplied user, tenant, workspace, project, or file IDs as
proof of authorization. Treat them as selectors that must be checked against
server-side identity and domain state.

File And Path Arguments
=======================

Raw path arguments passed to MCP tools are paths in the server process, not
paths on a remote client's machine. A remote client that sends
``/Users/alice/report.pdf`` is only sending a string; the server will interpret
that string relative to its own filesystem.

Prefer remote-safe arguments:

- file content, when payload size is appropriate;
- URLs for objects the server is allowed to fetch;
- object-store handles such as bucket/key pairs;
- application object IDs that the server resolves after authorization.

If a local stdio bridge accepts client-machine paths, the bridge should read
the file locally and send content, a URL, a handle, or an application object ID
to the remote MCP server. It should not forward the client path and expect the
remote server to read it.

If a server-side MCP tool intentionally accepts filesystem paths, restrict it
to explicit allowlisted roots, resolve symlinks, reject traversal outside those
roots, and authorize the operation against the authenticated caller before
opening the file.
