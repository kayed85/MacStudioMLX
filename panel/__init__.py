"""The panel's HTTP route handlers, one module per URL-prefix family.

Slice 4 of the restructuring (docs/ARCHITECTURE.md): the 101-route
if/elif chains in mlx_ltx_panel.py's do_GET/do_POST move here, a prefix
family at a time, registered in panel.routes' tables. The legacy chain
keeps serving whatever has not moved yet — dispatch tries the table
first and falls through.

Route modules never `import mlx_ltx_panel` (the panel usually runs as
__main__, and importing it by name would execute the whole 32k-line
module a second time — port bind, threads, everything). Instead each
module declares `P = None` and mlx_ltx_panel assigns the RUNNING module
object into it at import-wiring time, before the server starts.
"""
