# AI World Studio

Generador y editor de mundos 3D (paisajes de mundo abierto estilizados) **100 % Python**. El producto es el generador;
los mundos se **exportan como paquetes autocontenidos que se sueltan en cualquier proyecto de Godot 4** (probado en 4.7.2).
Se puede usar de cuatro formas, todas sobre el mismo núcleo (`core/world/session.py` + `core/world/service.py`):

| Forma | Comando |
|---|---|
| Editor de escritorio (PySide6) | `python awstudio.py ui` |
| Línea de comandos | `python awstudio.py generate highland_lake --size 1536 --seed 1 --godot-project <proyecto Godot>` |
| Agente IA (MCP, 52 herramientas) | `.mcp.json` ya incluido → `python -m mcp_server.server` |
| API REST | `python awstudio.py api` → http://127.0.0.1:8000/docs |

## Instalación
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install numpy scipy pillow PySide6 PyOpenGL fastapi uvicorn mcp pytest httpx
pytest -q          # 60+ pruebas, incluida una importación real en Godot si está instalado
```
Godot 4.7.x (editor) solo hace falta para verificar exportaciones y para re-hornear la biblioteca de props (`python -m exporters.godot.assets_bake`).

## Llevar un mundo a cualquier proyecto de Godot
El paquete exportado (carpeta) es independiente del proyecto que lo recibe:
- `World.tscn` contiene **solo el mundo** (terreno con LOD y colisión, agua, acantilados, vegetación) más `Markers/PlayerStart` y
  `Paths/Trail_*`, `Paths/River_*` (`Path3D`) que tu juego puede usar para aparición y rutas de NPC. **No trae sol, cielo ni cámara**: no choca con tu escena.
- `Preview.tscn` es un extra para ver el mundo al instante (sol, cielo, cámara). `Environment/stylized_environment.tres` es opcional.
- Rutas `res://` ajustables a cualquier carpeta: `--godot-project <proyecto> --subfolder addons/mi_mundo` o, después, `awstudio.py relocate <carpeta> --prefix res://otra/ruta/`.
- `--profile compatible` también funciona con el renderer Compatibility (verificado); `full` (por defecto) usa agua con profundidad y brillo (Forward+/Mobile).
- **Navegación**: `--navigation auto` (por defecto) hornea un `NavigationMesh` con el horneador de Godot (Recast) si encuentra el binario (`$AWS_GODOT`, la app de macOS o `godot` en PATH); `python` genera una malla gruesa propia (los senderos estrechos pueden desconectarse); `none` la omite.
  Verificado con el servidor de navegación real de Godot: del `PlayerStart` a la cima (`Markers/Summit`) hay ruta a pie en los mundos generados.
- **Hierba alta**: matas colocadas por la GPU (un parche compartido por chunk; el shader las apoya en el terreno con `Textures/height_rg.tres` y los splat maps, las oculta en agua, senderos y roca, y las encoge con la distancia). No se guarda nada por mata. `--no-grass` la desactiva.
- Salidas portables para otros flujos: `Textures/heightmap16.png` y `heightmap.r16` (importables por addons de terreno), `splat0-2.png`, `WorldData/manifest.json`.
- Se instancia como cualquier escena: `var w = load("res://addons/mi_mundo/World.tscn").instantiate(); add_child(w)`.

## Generar un mundo y verlo en Godot
```bash
# 1) generar + guardar + exportar dentro de TU proyecto de Godot (no toca su project.godot; crea <proyecto>/GodotWorld/)
python awstudio.py generate highland_lake --size 1536 --seed 7 --save MiMundo.awp --godot-project ~/proyectos/godot/mi-juego
# 2) abrir Godot (o re-escanear el sistema de archivos) y abrir res://GodotWorld/World.tscn
```
Otros comandos: `awstudio.py tools` (lista de herramientas), `awstudio.py call <herramienta> '<json>' --world X.awp --save Y.awp`,
`awstudio.py export X.awp --godot-project …`, `awstudio.py docs` (regenera `docs/TOOLS.md`).

## Qué genera hoy (preset `highland_lake`)
Cuenco con terrazas y paredes de roca, lago turquesa con isla, bosquecillos y praderas (robles redondos, pinos, arbustos, rocas junto a los acantilados),
un río que baja por las terrazas formando cascadas, anillo de montañas nevadas (decorativo, no transitable), una **montaña escalable** (rampa en espiral de pendiente constante hasta una cima plana, `create_climbable_mountain`) y senderos auto-enrutados. Análisis de transitabilidad (`analyze_traversability`)
y validación (`validate_world`). Look estilizado en Godot: hierba con variación, roca facetada, luz en bandas, agua con espuma, cielo con nubes.

## Herramientas (resumen; detalle en `docs/TOOLS.md`)
- **Mundo:** `get_capabilities`, `create_world`, `generate_landscape`, `set_world_style`, `save_world`, `load_world`, checkpoints, undo/redo.
- **Terreno:** `sculpt_terrain` (+ raise/lower/smooth/flatten), `apply_terrain_noise`, `create_mountain`, `create_mountain_range`, `create_valley`,
  `create_bowl`, `terrace_terrain`, `create_border_mountains`, `create_basin`, `create_plateau`, `create_cliff`, `apply_erosion`.
- **Features:** `create_river`, `create_river_auto` (el río busca su cauce cuesta abajo y cada precipicio que cruza se convierte en **cascada** con poza), `create_lake`, `create_path`, `create_trail` (auto-ruta), `create_climbable_mountain`, `analyze_traversability`.
- **Biomas/materiales:** `scatter_vegetation` (presets: oak_trees, pine_forest, bushes, cliff_rocks, …), `paint_terrain_material`.
- **Inspección:** `inspect_terrain`, `inspect_region`, `analyze_slope/elevation`, `validate_world`, `get_world_preview`, `capture_world_view` (aérea / jugador).
- **Godot:** `prepare_godot_export`, `export_to_godot` (`godot_project=` o `path=`), `get_export_status`.

Unidades: metros; origen en la esquina noroeste; x al este, y al sur.

## Estructura
`terrain/` (heightfield, pinceles, procedural, erosión, chunks) · `features/` (curvas, ríos, lagos, senderos, rutas) · `geometry/` (acantilados, mallas) ·
`biomes/` (máscaras, dispersión) · `materials/` · `validation/` · `render/` (vistas headless para agentes) · `exporters/godot/` (GLB, shaders, biblioteca de props, escena) ·
`core/` (sesión, historial, tareas, presets) · `api/` · `mcp_server/` · `ui/` + `app/` · `tests/` · `docs/`.

## Limitaciones conocidas
Sin hierba alta/flores, ríos y cascadas integrados entre terrazas, formaciones de roca singulares (arcos/pilares), navegación en Godot, edificios/props,
ni un editor de senderos en la UI. Detalle y decisiones en `docs/sprint1.md`. Plan maestro: `AI_World_Studio_Plan_Maestro_Python.md`.
