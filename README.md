# AI World Studio

Generador y editor de mundos 3D (paisajes de mundo abierto estilizados) **100 % Python**. El producto es el generador;
los mundos se **exportan como paquetes autocontenidos que se sueltan en cualquier proyecto de Godot 4** (probado en 4.7.2).
Se puede usar de cuatro formas, todas sobre el mismo núcleo (`core/world/session.py` + `core/world/service.py`):

| Forma | Comando |
|---|---|
| Editor de escritorio (PySide6) | `python awstudio.py ui` |
| Línea de comandos | `python awstudio.py generate highland_lake --size 1536 --seed 1 --godot-project <proyecto Godot>` |
| Agente IA (MCP, 56 herramientas) | `.mcp.json` ya incluido → `python -m mcp_server.server`. Con el editor abierto, el agente trabaja sobre el mundo del editor (panel «Agente») |
| API REST | `python awstudio.py api` → http://127.0.0.1:8000/docs |

## Instalación
```bash
python3 -m venv .venv && . .venv/bin/activate          # Windows: python -m venv .venv  y  .venv\Scripts\activate
pip install numpy scipy pillow PySide6 PyOpenGL fastapi uvicorn mcp pytest httpx
pytest -q          # 80+ pruebas, incluidas importaciones reales en Godot (y Terrain3D) si están instalados
```
Godot 4.7.x (editor) hace falta para verificar exportaciones, hornear navegación/Terrain3D y re-hornear la biblioteca de props
(`python -m exporters.godot.assets_bake`). Se busca en `$AWS_GODOT`, la app de macOS, `~/Documents|Downloads|Desktop/Godot_v4*_win64*.exe`
(en Windows se prefiere la variante `_console.exe`) y `godot`/`godot4` en el PATH. `.mcp.json` apunta a `.venv/Scripts/python.exe` (Windows;
en macOS/Linux cámbialo a `.venv/bin/python`).

## Llevar un mundo a cualquier proyecto de Godot
El paquete exportado (carpeta) es independiente del proyecto que lo recibe:
- `World.tscn` contiene **solo el mundo** (terreno con LOD y colisión, agua, acantilados, vegetación) más `Markers/PlayerStart` y
  `Paths/Trail_*`, `Paths/River_*` (`Path3D`) que tu juego puede usar para aparición y rutas de NPC. **No trae sol, cielo ni cámara**: no choca con tu escena.
- `Preview.tscn` es un extra para ver el mundo al instante (sol, cielo, cámara). `Environment/stylized_environment.tres` es opcional.
- Rutas `res://` ajustables a cualquier carpeta: `--godot-project <proyecto> --subfolder addons/mi_mundo` o, después, `awstudio.py relocate <carpeta> --prefix res://otra/ruta/`.
- `--profile compatible` también funciona con el renderer Compatibility (verificado); `full` (por defecto) usa agua con profundidad y brillo (Forward+/Mobile).
- **Navegación**: `--navigation auto` (por defecto) hornea un `NavigationMesh` con el horneador de Godot (Recast) si encuentra el binario (ver «Instalación»); `python` genera una malla gruesa propia (los senderos estrechos pueden desconectarse); `none` la omite.
  Verificado con el servidor de navegación real de Godot: del `PlayerStart` a la cima (`Markers/Summit`) hay ruta a pie en los mundos generados.
- **Hierba alta**: matas colocadas por la GPU (un parche compartido por chunk; el shader las apoya en el terreno con `Textures/height_rg.tres` y los splat maps, las oculta en agua, senderos y roca, y las encoge con la distancia). No se guarda nada por mata. `--no-grass` la desactiva.
- **Terrain3D** (`--terrain terrain3d`, herramienta `export_to_godot(terrain="terrain3d")`): en vez de chunks GLB, `World.tscn` lleva un nodo
  `Terrain3D` con regiones horneadas (`Terrain3D/data/terrain3d_*.res`), 8 texturas procedurales (`Terrain3D/textures`, `assets.tres`) y
  `material.tres` (sin fondo infinito). Así el terreno usa el LOD, la colisión y las herramientas de pintar/esculpir de Terrain3D. Requiere el addon:
  se toma de `<proyecto>/addons/terrain_3d` (o `--terrain3d-addon`, `$AWS_TERRAIN3D_ADDON`); las regiones las escribe Godot con ese mismo addon en una
  copia temporal, así que el formato siempre coincide con tu versión (probado: Terrain3D 1.1.0). Mapa de control: material dominante como base, el segundo
  como overlay, mezcla = proporción; bit de navegación fuera del agua. Para usar el aspecto de tu juego: `terrain3d_material` / `terrain3d_assets`
  (rutas `res://` de tu proyecto) y `terrain3d_texture_ids` (`{"grass": 0, "rock": 1, …}`: nuestros 8 materiales → tus ranuras). Agua, cascadas,
  vegetación, hierba, marcadores y navegación funcionan igual (la navegación se hornea desde la geometría de Terrain3D).
- **Agua para el sistema del juego**: siempre se escriben `WorldData/water.json` (ríos como cintas de muestras con cascadas simuladas columna a
  columna, lagos como celdas) y `WorldData/surface.r32` (altura del agua cada 4 m, para vadear/nadar). Con `water_script="res://…/water.gd"` el
  `World.tscn` lleva ese nodo (con `water_file`, `surface_file`, `origin`, `surf_cell`, `surf_size`) en lugar de nuestras mallas de agua. Probado con el
  `water.gd` de Wicktale: la heroína nada en el lago y las cascadas salen con su shader toon.
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
