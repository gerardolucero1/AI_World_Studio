# AI World Studio — guía para agentes (léelo primero)

**El protagonista es el generador, no Godot.** Godot es un destino de exportación: los mundos deben salir como paquetes autocontenidos que se sueltan
en cualquier proyecto de Godot 4 (`World.tscn` solo con el mundo; `Preview.tscn` opcional). No acoples el generador a un proyecto concreto.
El plan maestro es `AI_World_Studio_Plan_Maestro_Python.md` (§28 = estado y próximos pasos). Habla con el usuario en español.

Objetivo del usuario: generar escenarios tipo Genshin Impact / Zelda BOTW (mesetas, precipicios, ríos, cascadas, llanuras, bosques, senderos, montañas
escalables) y llevarlos a cualquier proyecto de Godot. Su juego (`prubas-3d`, en `~/Desktop/proyectos/godot/prubas-3d`) usa Terrain3D, jugador en capa 2,
Forward+, Jolt; ahí instalamos mundos de prueba en `generated_worlds/` (archivos nuevos, nunca modificar sus escenas ni `project.godot`).

## Cómo se usa
- Agente: herramientas MCP `ai-world-studio` (`.mcp.json`; si no aparecen, reiniciar la sesión) o CLI: `python awstudio.py call <tool> '<json>'`.
  `python awstudio.py tools` lista las 52 herramientas; `docs/TOOLS.md` las documenta (`python awstudio.py docs` lo regenera).
- Rápido: `python awstudio.py generate highland_lake --size 1024 --seed 3 --save X.awp --godot-project <proyecto> --subfolder generated_worlds/nombre`.
- UI: `python awstudio.py ui` (Archivo → Generar paisaje de ejemplo Ctrl+G; Exportar a Godot Ctrl+E). REST: `awstudio.py api`.
- Tests: `pytest -q` (≈1,5 min; incluye importaciones y navegación en Godot real si está en `/Applications/Godot.app` o `$AWS_GODOT`). Estado: 78 en verde.

## Mapa del código
`core/world/session.py` (WorldSession: operaciones, undo, checkpoints) y `service.py` (las 52 herramientas, única fuente para UI/CLI/REST/MCP) ·
`core/world/presets.py` (`highland_lake`) · `core/history/operations.py` (registro de operaciones, replay) · `terrain/` (heightfield, pinceles, procedural,
erosión, chunks) · `features/` (curvas, `rivers/river.py` con cascadas, lagos, `paths/` con `routing.py`: rutas, `route_river`, transitabilidad) ·
`geometry/cliffs` (muralla de mesetas) · `biomes/` (máscaras, dispersión) · `materials/` · `validation/` · `render/preview.py` (vistas headless para agentes) ·
`exporters/godot/` (`exporter.py`, `glb.py`, `terrain_lod.py`, `navigation.py`, `procedural_assets.py`, `assets_bake.py`, `library/*.res`, `shaders/`) ·
`api/` · `mcp_server/` · `ui/` + `app/` · `tests/`.

## Flujo recomendado para construir un mundo
1. `get_capabilities`. Unidades: metros; origen noroeste; x este, y sur; celdas ≈2 m; (vértices−1) divisible por 4.
2. `generate_landscape("highland_lake", size_m, seed)` o a medida: `create_world` → `apply_terrain_noise` → `create_bowl`/`create_mountain*` → `terrace_terrain` →
   `create_border_mountains` → `create_lake` → `create_river_auto` (cascadas) → `create_climbable_mountain` → `create_trail` → `scatter_vegetation(presets)`.
3. `create_checkpoint` antes de cambios grandes. 4. **Mira el resultado**: `capture_world_view` + `view_image`. 5. `validate_world` + `analyze_traversability`.
6. `save_world` y `export_to_godot(godot_project=…, subfolder=…)`; luego en Godot: re-escanear, instanciar `World.tscn` o abrir `Preview.tscn`.

## Reglas del dominio
- Las herramientas devuelven `{"ok": false, "error": …}`; corrige parámetros, no reintentes igual.
- Ríos/lagos/senderos dependen del terreno: si lo cambias después, rehazlos (aún no se marcan como desactualizados).
- No erosión térmica tras `terrace_terrain`. Rampas/senderos transitables ≥ 6 m de ancho. Montañas escalables: solo `create_climbable_mountain`
  (los senderos auto-enrutados por laderas muy empinadas hacen zigzags que se solapan; avisan `path_too_steep`).
- `export_to_godot` se niega a escribir en carpetas ajenas no vacías: no lo fuerces. Con `godot_project` no se toca `project.godot`.
- La navegación se hornea con Godot (Recast) en una **copia temporal**; la malla de Python (`navigation="python"`) es gruesa y desconecta senderos estrechos.

## Lecciones técnicas (no repetir estos errores)
- **Godot headless NO compila shaders ni dibuja**: para ver el resultado usa `screenshot_with_godot` (ventana real) o el editor vía MCP. Verifica siempre con capturas.
- No guardar pesos en el canal alfa de PNG (Godot "arregla" el RGB de píxeles transparentes → rampas negras): splat maps en RGB opaco (3 texturas).
- Texturas usadas en 3D pueden recomprimirse con pérdida: datos exactos (alturas) van en `.tres` ImageTexture (RG8 en base64), no en PNG.
- Colores de vértice en sRGB hay que convertirlos a lineal en el shader; la niebla volumétrica de 4.7 es muy fuerte (se quitó).
- `NavigationServer3D.map_get_path` corta a 4096 polígonos: usar `query_path` con `path_search_max_polygons` alto. El mapa tarda ~100 fotogramas en sincronizar.
- La colisión debe tener la misma resolución que la malla visual/navegación (`collision_step=1`), o el personaje se atasca en rampas.
- Ejecutar Godot sobre el proyecto receptor lo reimporta entero: usar `_temp_project` (copia temporal) para hornear/verificar.
- `MultiMesh` necesita `custom_aabb` si el shader desplaza vértices; la hierba va por GPU (nada por mata en la escena).
- En macOS `sed -i` necesita `''`; con bloques largos usar scripts de Python para editar. Un `&&` tras un comando que falla salta TODA la edición siguiente (pasó una vez).
- MCP `godot-ai`: `game_eval` aborta a los 8 s (y deja el juego en depurador: parar y relanzar); el juego se congela en segundo plano (acelerar con
  `Engine.time_scale`); `editor_screenshot` falla si la imagen supera ~1 MB (bajar `max_resolution`).

## Pendiente (orden sugerido; detalle en el plan §28)
1. **Exportar a Terrain3D** (regiones + texturas) para usar el terreno y el shader toon del juego del usuario. 2. Flores y más hierba (interacción, estilo del juego).
3. Formaciones de roca (arcos, pilares, salientes); acantilados como geometría real en las terrazas. 4. Más presets (cañón, costa, pradera abierta).
5. Editar features creados (`modify_*`), dependencias y aviso de "desactualizado"; reordenar/desactivar operaciones. 6. UI: editor de curvas/senderos, overlays, panel de actividad del agente.
7. Cascadas con colisión/sonido/salpicaduras; ríos que se unen. 8. Validador completo, AWP completo, procedencia de assets, probar otras versiones de Godot (solo 4.7.2 probado).

## Desarrollo
Shaders: `exporters/godot/shaders/`. Props procedurales: `procedural_assets.py` → `python -m exporters.godot.assets_bake` (necesita Godot) → `library/*.res` (commit).
Documentación viva: `README.md`, `docs/sprint1.md` (decisiones y límites por fase), `docs/TOOLS.md`. Repo: github.com/gerardolucero1/AI_World_Studio (rama `main`).
