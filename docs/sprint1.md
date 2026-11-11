# Sprint 1: estado y decisiones

Ejecutar: `. .venv/bin/activate && python app/main.py` (Windows: `.venv\Scripts\activate`) · Tests: `pytest` · Benchmark: `python scripts/benchmark.py`

## Decisiones (plan §25)
- **Viewport:** `QOpenGLWidget` (PySide6) + PyOpenGL, core profile 3.3. Sin dependencias extra de visor.
- **Heightfield:** `float32` (ny, nx), `cell_size` en metros. Eje Y = altura, fila de grid = Z.
- **Chunks:** 64 celdas (65x65 vértices, bordes compartidos; normales con vecinos reales => sin costuras).
- **Actualización parcial:** cada dab devuelve un rect sucio -> solo se reconstruyen esos chunks y se re-suben con `glBufferSubData`.
- **Undo/redo:** snapshots por tile de 64x64 solo de lo tocado en el trazo.
- **AWP:** `project.json` + `terrain/heightfields/base.npy` (carpetas del plan creadas).

## Medido (CPU, dab r=40 celdas + rebuild de chunks sucios)
Dab ~0.03-0.06 ms; rebuild 4-6 chunks ~0.5-0.7 ms, independiente de la resolución total (513..4097).
El render GL real (fps con GPU) falta medirlo en la máquina del usuario: el panel inferior muestra ms/frame.

## Límites conocidos
- Picking por ray-march en Python (suficiente a 60 Hz; optimizable con jerarquía min/max).
- Sin LOD ni frustum culling (todos los chunks se dibujan).
- Sin máscaras, simetría ni brushes Noise/Terrace (fases siguientes).
- El snapshot de undo guarda tiles completos de 64x64 (pico de memoria en trazos largos).

## Fase 2 (arranque)
- `terrain/procedural`: ruido fBm con semilla, montaña, cordillera, meseta (polígono), cuenca, valle.
- `terrain/erosion`: térmica (conserva masa) e hidráulica aproximada, vectorizadas. 1025²: 30 it. térmica 0.33 s, 40 it. hidráulica 0.9 s.
- `core/history/operations.py`: operaciones serializables (`ops.json` en el .awp) con versión de algoritmo.
- UI: botones en el panel para cada operación; cada una es un paso de undo.
- Límite: el log de operaciones se guarda, pero aún no se reconstruye desde él al abrir (se carga el heightfield),
  y deshacer no lo recorta. Reordenar/desactivar operaciones queda para después.
- La meseta aún es heightfield con rampa empinada; las paredes reales llegan en la fase 4.

## Fase 3 (arranque)
- `features/curves`: Catmull-Rom con remuestreo por longitud de arco, muestreo de altura y rejilla de influencia (KD-tree).
- **Río:** cauce monótono (nunca sube aguas abajo), perfil parabólico, orillas suaves, nivel de agua por punto.
  Avisa `river_uphill` si el terreno sube contra el flujo.
- **Lago:** cuenca dentro de un polígono; nivel por defecto = borde más bajo (sin fugas). Avisa `lake_leak` / `lake_dry_cells`.
- **Sendero:** pendiente longitudinal limitada (`max_slope`), corte/relleno; avisa `path_too_steep` sugiriendo zigzags/rampas/escalones.
- Los features se guardan como operaciones con parámetros + `result` (niveles de agua, avisos); el viewport dibuja sus curvas.
- Límites: sin superficie de agua real (solo líneas guía), sin cascadas, sin reconstrucción por dependencias
  (aún no marca el cauce como desactualizado si el terreno cambia), ríos no se unen entre sí ni desembocan automáticamente en lagos.

## Fase 4 (arranque): acantilados como geometría real
- `geometry/cliffs/cliff.py`: muralla de meseta = anillo extruido con estratos (repisas), caperuza voladiza (overhang), ensanche en la base y ruido;
  la fila inferior se muestrea del heightfield (apoyada, sin flotar). Operación `cliff_plateau` = tope en heightfield con rampa estrecha + malla independiente.
- Validación: `cliff_degenerate`, `cliff_detached` (terreno cambió bajo la pared), `cliff_low`.
- Las mallas son datos de runtime (`result["_mesh"]`); no se serializan: se regeneran al reproducir las operaciones.
- Límites: solo paredes de mesetas (sin cuevas, arcos ni formaciones aisladas); UVs generados pero sin material/textura; sin LOD.

## Corrección importante
Hasta la fase 3 el visor **no dibujaba** (el shader fallaba la validación en core profile y Qt ocultaba la excepción).
Corregido con `validate=False`; ahora se verificó con captura de framebuffer (`grabFramebuffer`).

## Fase 5 (arranque): biomas, vegetación y materiales
- `biomes/masks`: pendiente, máscara de agua (ríos/lagos desde los resultados de las operaciones), de senderos y distancia al agua.
- `materials`: 8 materiales (grass, dirt, rock, snow, sand, mud, wet_rock, gravel). Pesos por reglas (roca por pendiente, nieve por altitud,
  arena/barro/roca húmeda cerca del agua, tierra en senderos) + pintura manual (`paint_material`) que se reproduce desde el log.
  `final_weights()` es la fuente de verdad (exportación a máscaras); el shader del visor aproxima las reglas (grass/rock/snow) y aplica la pintura como texturas.
- `biomes/scatter`: dispersión determinista por semilla con reglas de altitud, pendiente, distancia al agua, densidad, clusters (ruido), separación,
  región y zonas de exclusión. Validación: `veg_in_water`, `veg_steep`, `veg_too_many`.
- Visor: instancias dibujadas con instancing (un draw call; 23k instancias a ~0.8 ms/frame en la captura de prueba).
- Undo/redo ahora recorta/restaura el log de operaciones (resuelve el desajuste anotado en la fase 2).
- Límites: formas de vegetación = conos placeholder (sin modelos GLB), sin LOD/culling por chunk, la pintura solo por botón demo
  (aún sin pincel interactivo de material), sin biomas con nombre ni `set_biome_rules`.

## Fase 6 (arranque): API y MCP
- `core/world/session.py` (**WorldSession**): servicio único (operaciones, undo/redo, checkpoints, guardar/cargar). La UI (`TerrainViewport`) y la API/MCP lo usan; no hay lógica duplicada.
  Al cargar un proyecto se regeneran los datos de runtime (mallas de acantilado, instancias de vegetación) sin reaplicar ediciones.
- `core/world/service.py`: 39 herramientas semánticas en metros (create_world, create_mountain/range/valley/basin/plateau/cliff, apply_erosion,
  create_river/lake/path, scatter_vegetation, paint_terrain_material, inspect_terrain/region, analyze_slope/elevation, validate_world,
  get_world_preview, capture_world_view, create_checkpoint/rollback/compare, undo/redo, tareas). Devuelven `{"ok": False, "error"}` en vez de lanzar.
- Checkpoint automático antes de operaciones grandes (se conservan los últimos 5). Tareas en segundo plano: `queued/running/completed/failed/cancelled`.
- `api/app.py` (FastAPI): `GET /tools`, `POST /tools/{name}`. Ejecutar: `uvicorn api.app:app`.
- `mcp_server/server.py` (MCPServer, mcp 2.x): mismas herramientas + `view_image`. Ejecutar: `python -m mcp_server.server` (stdio).
  Config de cliente: `{"command": ".venv/bin/python", "args": ["-m", "mcp_server.server"], "cwd": "<ruta del proyecto>"}`.
- `render/preview.py`: renderizador de software (ray-marching + splats de geometría y vegetación) para que el agente inspeccione vistas aéreas y a nivel de jugador.
- `validation/validator.py`: hallazgos con severidad, ubicación, explicación y sugerencia.
- Límites: servidor MCP y UI son procesos separados (cada uno con su propia sesión; para compartir mundo hay que guardar/cargar .awp);
  el render de software tarda ~4-6 s por imagen y los árboles muy cercanos salen punteados; sin autenticación en la API (solo localhost);
  sin modos Assisted/Autonomous/Batch ni política de autocorrección automatizada (el agente decide, con checkpoints y compare/rollback).

## Fase 7: exportador a Godot 4.7.2
`exporters/godot/` (menú Archivo → Exportar a Godot, Ctrl+E; herramientas `prepare_godot_export`, `export_to_godot`, `get_export_status`).
Genera una carpeta que se abre directamente como proyecto de Godot (o se copia dentro de uno con `res_prefix="res://GodotWorld/"`):
- `Terrain/Chunk_XX_YY.glb`: por chunk, 4 LOD (`Terrain_LOD0..3`, con faldones que tapan las grietas entre LOD) + `Collision-colonly`
  (Godot lo importa como `StaticBody3D` + malla de colisión). Rangos de visibilidad en `World.tscn`.
- `Features/Lakes|Rivers/*.glb` (superficie de agua con `Materials/water.tres`), `Geometry/Cliff_N.glb` (pared con colisión).
- `Vegetation`: un `MultiMeshInstance3D` por especie y chunk (mallas provisionales: cono/esfera) con distancia de visibilidad.
- `Materials/terrain.tres` + `terrain.gdshader`: mezcla de 8 materiales con 3 splat maps RGB (sin alfa; ver nota) + `Textures/heightmap16.png`.
- `World.tscn` (cielo, sol, cámara incluidos), `project.godot`, `WorldData/manifest.json` (tamaños, LOD, hashes SHA-1, validación).
- Validación de exportación: chunks y colisiones, materiales/texturas, referencias rotas, duplicados, nº de instancias.
- Se niega a escribir en una carpeta no vacía que no sea una exportación anterior.

**Verificado con Godot 4.7.2 real** (`verify_with_godot`, `screenshot_with_godot`): importa sin errores, 16 chunks × 4 LOD, colisiones, 6473 MultiMesh,
materiales aplicados y render correcto. Hallazgos corregidos gracias a ello: formato `PackedVector3Array` en el `.tres`, y el canal alfa de los splat maps
(Godot corrompe el RGB de píxeles transparentes al importar → rampas negras); ahora los pesos van en texturas RGB opacas.

Límites: sin navegación (`Navigation/` vacía), sin cascadas ni senderos como geometría (los senderos solo como material de suelo),
vegetación con mallas provisionales (sin biblioteca GLB), LOD de distancia fija sin streaming, el agua usa un material transparente simple (sin shader de olas),
el orden de las transformaciones de MultiMesh no se pudo comprobar en modo headless (renderer ficticio) pero sí en el render real; resolución: (vértices−1) divisible por 4.

## Fase 8: estilo "open world estilizado" (referencia Genshin)
- **Terreno:** `terrace` (bancales planos separados por paredes empinadas, con máscara radial), `bowl` (cuenco rodeado de tierras altas),
  `border_mountains` (anillo de montañas con esquinas redondeadas que oculta el borde del mapa), `set_world_style` (línea de nieve, umbral de roca).
- **Senderos y escalada:** `features/paths/routing.py`: enrutamiento Dijkstra de menor pendiente (rodea agua, cruza acantilados por el punto más suave y
  el sendero excava una rampa), `create_trail`; `analyze_traversability` dice qué zonas y qué cima se alcanzan a pie y qué regiones quedan aisladas.
- **Generador:** `generate_landscape("highland_lake", size_m, seed)` (menú Archivo → Generar paisaje, Ctrl+G): cuenco con terrazas, lago con isla,
  bosquecillos y praderas, rocas en la base de los acantilados, anillo de montañas nevadas y senderos que suben por las terrazas hasta una cima.
- **Look en Godot** (`exporters/godot/shaders/`): terreno con hierba de dos tonos y variación, roca facetada oscura en caras verticales/clara en repisas,
  luz en bandas (toon), agua turquesa con espuma y profundidad, cielo con nubes, sol cálido, brillo y niebla suave opcional.
- **Biblioteca de props procedural** (`procedural_assets.py` + `assets_bake.py` → `exporters/godot/library/*.res`): 3 robles redondos, 2 pinos, 2 arbustos,
  4 rocas. Se hornean con Godot una vez y se copian al exportar. Color por instancia (otoño verde/amarillo/naranja) con `MultiMesh.use_colors`.
- **Dispersión:** grupos/claros (`clusters` con umbral), `min_slope`, `cliff_dist` (rocas junto a paredes).
- Hallazgos corregidos gracias a capturas en Godot real: niebla demasiado densa; colores de vértice en sRGB tratados como lineales (árboles pálidos);
  granulado en rocas lejanas (detalle con fade por distancia).
- **Pendiente para el nivel "Genshin":** hierba alta y flores (GPU), ríos/cascadas integrados entre terrazas, formaciones de roca hero (arcos, pilares),
  mallas de árbol de mayor calidad, edificios/props, navegación (NavigationMesh) y personaje para probar la escalada en Godot, UI para trazar senderos automáticos.


## Ríos con cascadas (fase 8, continuación)
- `features/rivers/river.py`: detecta caídas (descenso ≥ 1,2 de pendiente con ≥ 5 m de desnivel), respeta la pared (no la excava), registra `falls`
  (cima/base/altura/ancho) y `fall_segments`, y talla una poza al pie de cada caída. El cauce sigue siendo monótono.
- `features/paths/routing.py::route_river`: enrutador dirigido que solo baja (admite subidas pequeñas con coste) y busca el precipicio más bajo
  para cruzarlo (penaliza la pendiente, no el desnivel por paso). Herramienta `create_river_auto`.
- Exportación: cinta de agua interrumpida en las caídas + `River_N_Fall_K.glb` (lámina con UV de caída) con `waterfall.gdshader`
  (rayas de espuma que bajan, borde suave, espuma al pie). Verificado con capturas en Godot real.
- Límites: las láminas de cascada no tienen colisión ni sonido ni partículas de salpicadura; el agua no bloquea la navegación;
  el último tramo hacia el lago suele avisar `river_uphill` (corta el borde del lago).


## Hierba alta (fase 8, continuación)
- Malla de mata procedural (`grass_tuft`, 8 hojas afiladas) horneada con Godot; `grass.gdshader` coloca cada mata en el shader:
  altura bilineal desde `Textures/height_rg.tres` (RG8 en un `.tres` con base64, **sin importación**, para que Godot no la recomprima con pérdida),
  cobertura desde el splat (R de `splat0`), agua desde la máscara nueva (B de `splat2`), color con la misma variación que el terreno, viento y fundido con la distancia.
- Un `MultiMeshInstance3D` por chunk con hierba (≥ 4 % de cobertura), centrado en el chunk, `custom_aabb` (el culling no sabe de la elevación del shader),
  `cast_shadow = 0` y 3 parches con distinto azar para que no se note el patrón. ~5–9 k matas por chunk según `grass_spacing`.
- Límites: sin flores, sin interacción con el jugador (no se aplasta), tope de distancia 68 m (ajustable), coste de vértices en pantalla alto con densidades muy altas;
  el estilo (colores) sigue al de mi shader de terreno, no al de tu `terrain3d_toon`.


## Exportar a Terrain3D (fase 9) y paso a Windows
- `exporters/godot/terrain3d.py` + `ExportSettings(terrain="terrain3d")`. Python prepara altura float32 (metros), mapa de control uint32
  (base `<<27`, overlay `<<22`, mezcla `<<14`, navegación `<<1`; mismo formato que `DECODE_*` en los shaders del addon) y 8 texturas RGBA8 tileables
  (albedo + altura en A; normal OpenGL + rugosidad en A). Godot, con el addon real copiado a un proyecto temporal, hace `import_images`,
  `save_directory`, guarda las texturas como `ImageTexture` `.res` con mipmaps (exactas, sin paso de importación) y `assets.tres`/`material.tres`
  con `ResourceSaver` (el formato de las regiones depende de la versión del addon: no se escribe desde Python).
- Tamaño de región: el que menos relleno deja con ≤ 16 regiones por eje desde el origen (el mapa de regiones es 32×32, -16..15; el mundo empieza en 0,0).
  `change_region_size` hay que llamarlo con el nodo ya en el árbol (asignar `region_size` antes no surte efecto).
- Comprobación al hornear: alturas leídas de vuelta con `data.get_height` en vértices exactos (error ~1e-14 m). Entre vértices Terrain3D interpola
  por triángulo y `Heightfield.sample` bilineal (difieren hasta ~0,3 m con ruido): no comparar ahí.
- En juego (no editor) Terrain3D construye los arrays de texturas y **vacía `texture_list`** (`free_editor_textures`): para verificar mira
  `assets.get_albedo_array_rid().is_valid()`, no el número de texturas.
- Sin cámara (headless) Terrain3D avisa `Cannot find clipmap target or active camera`: ruido benigno en verificación.
- Navegación: `generate_nav_mesh_source_geometry(aabb, true)` (respeta el bit de navegación pintado) se añade a la geometría fuente del horneado.
- Verificación nueva (ambos modos): rayo bajo `PlayerStart` → hay colisión real (`ground_under_start`, `ground_gap_m` ≈ 1 m).
- Las copias temporales (`_temp_project`) llevan el addon si el paquete es Terrain3D; `relocate_package` no toca `addons/`. Exportación autónoma:
  se copia el addon (todas las plataformas) y se activa en `project.godot`.
- Windows: `exporters/godot/godot_bin.py::find_godot` (también Documents/Downloads/Desktop, prefiere `_console.exe`), `.mcp.json` con
  `.venv/Scripts/python.exe`. `export_to_godot(verify=True)` ya no abre el proyecto receptor: verifica en copia temporal.
- Material del juego: `terrain3d_material` puede ser una escena (`res://tallow_hills.tscn`); `extract_material` copia su `Terrain3DMaterial`
  con las ext/sub resources que usa (sin `uid`, rutas del proyecto intactas) y fuerza `world_background = 0` (si no, el plano infinito del juego rodea el mundo).
  `copy_external_deps` lleva a las copias temporales los archivos del proyecto receptor que el paquete referencia (shader, texturas, `#include`).
  Probado en `wicktale` (el juego del usuario en Windows): senderos → id 1 (`terrain3d_texture_ids`), verificación y capturas sin abrir el proyecto.
- Límites: las texturas son procedurales y planas de estilo (no el toon de tu juego salvo que pases tu material/assets); el sendero tallado no se marca
  como textura propia (sale como `dirt`); probado solo con Terrain3D 1.1.0 + Godot 4.7.2 en Windows.
