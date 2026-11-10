# AI World Studio — guía para agentes

**El protagonista es el generador, no Godot.** Godot es un destino de exportación: los mundos deben salir como paquetes autocontenidos
que se sueltan en cualquier proyecto de Godot 4 (`World.tscn` solo con el mundo; `Preview.tscn` es opcional). No acoples el generador a un proyecto concreto.

Este repo es un generador de mundos 3D. Para construir un mundo **usa las herramientas MCP `ai-world-studio`** (ver `.mcp.json`; si no aparecen, reinicia
la sesión de Claude Code) o la CLI `python awstudio.py call <tool> '<json>'`. No edites arrays de alturas ni escribas GDScript para crear terreno.

## Flujo recomendado
1. `get_capabilities` (convenciones, presets, flujo). Unidades: metros; origen noroeste; x este, y sur.
2. Rápido: `generate_landscape("highland_lake", size_m, seed)`. A medida: `create_world` → `apply_terrain_noise` → `create_bowl`/`create_mountain*` →
   `terrace_terrain` (paredes) → `create_border_mountains` → `create_lake`/`create_river` → `create_trail` → `scatter_vegetation(presets)`.
3. `create_checkpoint` antes de cambios grandes; `rollback_to_checkpoint` / `compare_world_versions` si empeora.
4. **Mira el resultado**: `capture_world_view` (aérea y de jugador) y `view_image`; no te fíes solo de números.
5. `validate_world` + `analyze_traversability`; actúa sobre `suggestion` de cada aviso o díselo al usuario.
6. `save_world` y `export_to_godot(godot_project="<carpeta con project.godot>", subfolder="…")` → escribe `<proyecto>/<subfolder>/` sin tocar `project.godot`.
   Luego en el editor de Godot: re-escanear; instanciar `World.tscn` (o abrir `Preview.tscn` para verlo). `profile="compatible"` si el juego usa el renderer Compatibility.

## Reglas
- Las herramientas devuelven `{"ok": false, "error": …}`; no las reintentes igual: corrige parámetros.
- Orden importa: ríos/lagos/senderos dependen del terreno; si cambias el terreno después, rehaz esos features (aún no se marcan como desactualizados).
- No ejecutes erosión térmica tras `terrace_terrain` (desmorona las paredes).
- Montañas escalables: usa `create_climbable_mountain` (rampa ≥ 6 m de ancho). Los senderos auto-enrutados sobre laderas muy empinadas hacen zigzags que se solapan; si `create_trail` avisa `path_too_steep`, replantea la ruta.
- La navegación se hornea con Godot al exportar (`navigation="auto"`); la malla de Python es gruesa y puede desconectar senderos estrechos.
- `export_to_godot` se niega a escribir en carpetas ajenas no vacías: no lo fuerces.
- Tamaños típicos: mundo 1024–2048 m, celdas ≈2 m. Resolución: (vértices−1) divisible por 4.

## Desarrollo
`pytest -q` (≈1 min con la prueba de Godot real). Shaders en `exporters/godot/shaders/`; props procedurales en `exporters/godot/procedural_assets.py`
(re-hornear con `python -m exporters.godot.assets_bake`). Documentación de herramientas: `python awstudio.py docs`.
