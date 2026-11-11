# AI World Studio — guía para agentes (léelo primero)

**El protagonista es el generador, no Godot.** Godot es un destino de exportación: los mundos salen como paquetes autocontenidos que se sueltan
en cualquier proyecto de Godot 4 (`World.tscn` solo con el mundo; `Preview.tscn` opcional). No acoples el generador a un proyecto concreto.
El plan maestro es `AI_World_Studio_Plan_Maestro_Python.md` (§28 = estado y próximos pasos). Habla con el usuario en español.

Objetivo del usuario: escenarios tipo Genshin Impact / Zelda BOTW (mesetas, precipicios, cañones, ríos, cascadas, llanuras, bosques, senderos, montañas
escalables) llevados a cualquier proyecto de Godot. Evaluación honesta (11/2026): la **forma** del terreno ya está a nivel de "blockout" bueno y jugable;
lo que separa de Genshin es el **acabado** (roca esculpida en paredes y montañas, assets, pasto/flores del juego), no la topografía.

## Entorno (Windows)
- Venv: `.venv/Scripts/python.exe` (`python -m venv .venv` + `pip install numpy scipy pillow PySide6 PyOpenGL fastapi uvicorn mcp pytest httpx`).
- Godot 4.7.2 en `~/Downloads/Godot_v4.7.2-stable_win64.exe/…_console.exe`; lo encuentra `exporters/godot/godot_bin.find_godot` (o `$AWS_GODOT`).
- Juego del usuario: Bowgame / *Wicktale* en `~/Desktop/Proyectos/Godot/wicktale` (en macOS era `prubas-3d`). Terrain3D 1.1.0, jugador en capa 2,
  Forward+, Jolt. Probamos mundos en su `generated_worlds/` (solo archivos nuevos; nunca tocar sus escenas ni `project.godot`). Su editor suele estar
  abierto con el MCP `godot-ai` (sesión `wicktale@…`): antes de detener una partida suya, pregunta.
- Rama de trabajo: `terrain3d-windows` (aún sin fusionar en `main`). Repo: github.com/gerardolucero1/AI_World_Studio.

## Cómo se usa
- Editor: `python awstudio.py ui`. «🏞 Generar paisaje» (Ctrl+G: cuenco con lago, valle de mesetas, valle fluvial, cañón + tamaño + semilla),
  «Exportar a Godot» (Ctrl+E; detecta Terrain3D, material y agua del juego), herramientas de esculpir/crear, «Colocar objeto», «Editar ríos, lagos y
  senderos» (asas), panel **Historial** (editar parámetros o desactivar cualquier operación), **capas de análisis** (altura, pendiente, agua,
  transitable), panel **Agente**.
- Agente: MCP `ai-world-studio` (`.mcp.json` → `.venv/Scripts/python.exe -m mcp_server.server`; si no aparece, reiniciar la sesión de Claude Code).
  **Con el editor abierto**, el servidor MCP reenvía cada herramienta al editor (`core/world/bridge.py`; `~/.ai_world_studio/editor.json` con puerto y
  token, solo 127.0.0.1): agente y usuario trabajan sobre el MISMO mundo y el panel «Agente» muestra cada llamada. `AWS_EDITOR=off` lo desactiva.
- CLI: `python awstudio.py generate mesa_valley --size 1024 --seed 3 --save X.awp --godot-project <proyecto> --subfolder generated_worlds/x
  --terrain terrain3d` · `awstudio.py call <tool> '<json>'` · `awstudio.py tools` (56 herramientas; `docs/TOOLS.md`, `awstudio.py docs` lo regenera).
- Tests: `pytest -q` (117 en verde, ≈3,5 min; incluyen Godot real, Terrain3D si encuentra el addon —`$AWS_TERRAIN3D_ADDON` o un proyecto en
  `~/Desktop/Proyectos/Godot/*`—, el editor PySide6 sin pantalla y el puente editor↔agente).

### Receta para el juego del usuario (Wicktale)
`export_to_godot(godot_project=<wicktale>, subfolder="generated_worlds/<nombre>", terrain="terrain3d", terrain3d_material="res://tallow_hills.tscn",
terrain3d_texture_ids={"dirt": 1, resto: 0}, water_script="res://scripts/world/water.gd")` — o el diálogo del editor, que lo detecta solo
(`inspect_godot_project`). Su material es un `Terrain3DMaterial` incrustado en `tallow_hills.tscn` con `shader_override` `terrain3d_toon.gdshader`
(pinta camino donde la textura base es la id 1). Escena de prueba: copiar el Player de `world_test.tscn`, `start_yaw = atan2(dx, dz)` (el modelo mira
a +Z), su sol y `world_env_open.tres`. Hay ejemplos en `generated_worlds/test_*.tscn`. Para juzgar el aspecto, capturar con SU entorno, no con
`Preview.tscn` (lava los colores toon).

## Mapa del código
`core/world/service.py` (las 56 herramientas, única fuente para UI/CLI/REST/MCP) · `session.py` (WorldSession: operaciones, undo/redo, checkpoints,
`origin` + `modify` para editar operaciones pasadas, `reground`) · `presets.py` (`highland_lake`) + `presets_extra.py` (`mesa_valley`, `river_valley`,
`canyon`) · `bridge.py` (editor↔MCP) · `core/history/operations.py` (registro de operaciones, replay; `brush` = pincelada del editor) ·
`terrain/` · `features/` (`curves/edit.py` asas, `rivers`, `lakes`, `paths/routing.py`) · `geometry/cliffs` · `biomes/` (máscaras, `scatter`,
`place.py` objetos sueltos) · `materials/` · `render/` (`preview.py`, `overlays.py`) · `exporters/godot/` (`exporter.py`, `terrain3d.py`,
`game_water.py`, `host_project.py`, `godot_bin.py`, `glb.py`, `navigation.py`, `procedural_assets.py`, `library/*.res`, `shaders/`) ·
`app/` (`main.py`, `export_dialog.py`, `op_editor.py`, `agent_panel.py`) + `ui/viewport/` · `api/` · `mcp_server/` · `tests/`.

## Flujo recomendado para construir un mundo
1. `get_capabilities`. Unidades: metros; origen noroeste; x este, y sur; celdas ≈2 m; (vértices−1) divisible por 4.
2. `generate_landscape(preset, size_m, seed)` o a medida (`create_world` → relieve → montañas/mesetas/valles → `terrace_terrain` → agua → senderos →
   `scatter_vegetation` / `place_objects`). Para cambiar algo ya hecho: `modify_operation(i, params)` / `set_operation_enabled` (ver `list_operations`).
3. `create_checkpoint` antes de cambios grandes. 4. **Mira el resultado** (`capture_world_view` + `view_image`, o capturas en Godot). 5. `validate_world`
   + `analyze_traversability`. 6. `save_world` y `export_to_godot(...)`.

## Reglas del dominio
- Las herramientas devuelven `{"ok": false, "error": …}`; corrige parámetros, no reintentes igual. Una ruta fallida (`create_river_auto`, `create_trail`) no aplica nada.
- Todo cambio de terreno debe quedar como operación (si no, `modify` lo pierde al repetir el historial). Proyectos `.awp` sin `origin.npy` no se pueden editar.
- No erosión térmica tras `terrace_terrain`. Senderos ≥ 6 m. Montañas escalables: `create_climbable_mountain` (los senderos por laderas muy empinadas
  hacen zigzags que se solapan: `path_too_steep`).
- `export_to_godot` no escribe en carpetas ajenas no vacías; con `godot_project` no toca `project.godot`. Nunca abrir Godot sobre el proyecto receptor:
  hornear/verificar/capturar en copia temporal (`_temp_project`, ya lo hacen exportador, verificación y capturas).

## Lecciones técnicas (no repetir estos errores)
- **Godot headless NO compila shaders ni dibuja**: mirar con `screenshot_with_godot` (ventana real) o el editor vía MCP. Verificar siempre con capturas.
- Terrain3D: las regiones las escribe Godot con el addon del proyecto (copia temporal), nunca Python. `change_region_size` con el nodo ya en el árbol.
  En juego vacía `texture_list` tras crear los arrays (verificar con `get_albedo_array_rid`). Comparar alturas solo en vértices (interpola por triángulo).
  Un material extraído de una escena lleva `world_background = 0` (si no, plano infinito alrededor). Las copias temporales traen las dependencias
  externas (`copy_external_deps`, sigue `.tres/.gdshader/.tscn/.gd`).
- Agua del juego (`game_water.py`): nuestros cauces son poco profundos y van por terrazas → orilla con tope (ancho/2 + 4 m) y cortada en precipicios;
  cascadas con el ancho de aguas arriba; agua que cae en un escalón casi a nivel de la poza termina ahí; celdas de río con suelo > 4,5 m bajo el agua
  no son agua (si no, se nada en el aire).
- Rendimiento: `water_mask` rasteriza lagos con PIL en su caja (antes 10 s, ahora 0,16 s). Si algo es lento, perfilar: suele ser un cálculo por celda
  sobre todo el mapa.
- Presets: un río no puede subir a una isla (apuntar a la orilla); rutas opcionales con reintentos; el jugador empieza en tierra firme (`_dry_point`);
  la pintura de materiales tapa también las paredes (pintar suave para que conserven la roca).
- Editor: comparte `service.SESSION` (antes «Generar» iba a una sesión oculta). Clientes MCP lanzan el servidor con entorno mínimo: variables propias
  hay que pasarlas en `env`. Qt `QWidget.grab()` en plataforma `offscreen` no tiene fuentes: capturas de UI con la plataforma normal.
- No pesos en el alfa de PNG (Godot altera el RGB de píxeles transparentes): splat en RGB opaco. Datos exactos (alturas) en `.tres` ImageTexture, no PNG.
  Colores de vértice sRGB → lineal en el shader. `map_get_path` corta a 4096 polígonos: `query_path` con límite alto. Colisión a la misma resolución
  que la malla (`collision_step=1`). `MultiMesh` con shader que desplaza → `custom_aabb`.
- Edición de archivos: bloques largos con scripts de Python en el scratchpad (los heredocs de bash rompen comillas y `\n`).
- MCP `godot-ai`: `game_eval` aborta a los 8 s; `editor_screenshot` falla si la imagen pasa ~1 MB (bajar `max_resolution`); `project_run(autosave=False)`
  para no guardar escenas del usuario; `game_manage input_sequence` (acciones `move_forward`, `run`, `jump`) para mover al jugador.

## Pendiente (orden sugerido; detalle en el plan §28.6)
1. **Calidad visual (lo que más falta para Genshin)**: revestir paredes de mesetas/terrazas y montañas con piezas de roca 3D (el juego tiene un
   catálogo `rock_cliff`); montañas con crestas/erosión; usar `flora.gd` y `grass_field.gd` del juego (árboles con colisión, pasto y flores).
2. Senderos con su propia id de textura; ríos que se unen; cascadas con colisión/sonido.
3. Más presets: costa, alta montaña/glaciar, islas. Formaciones de roca (arcos, pilares, salientes, cuevas).
4. Editor: pinceles nuevos (sello, ruido, terraza, máscaras), biblioteca de assets, modo «asistido» (el agente propone, el usuario aprueba),
   dependencias con aviso de «desactualizado».
5. Calidad: validador completo, AWP por capas, rendimiento en mapas grandes, otras versiones de Godot, procedencia de assets.

## Desarrollo
Shaders: `exporters/godot/shaders/`. Props procedurales: `procedural_assets.py` → `python -m exporters.godot.assets_bake` (necesita Godot) → `library/*.res`.
Documentación viva: `README.md`, `docs/sprint1.md` (decisiones y límites por fase), `docs/TOOLS.md`.
