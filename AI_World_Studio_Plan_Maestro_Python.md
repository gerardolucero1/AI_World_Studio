# AI World Studio --- Plan Maestro

## Editor híbrido de mundos 3D para videojuegos, desarrollado completamente en Python

**Versión:** 1.1 (el estado de implementación y los próximos pasos están en el §28)\
**Fecha:** 10 de noviembre de 2026\
**Destino de exportación:** Godot 4 (probado en 4.7.2)

> **Retomar aquí:** lee el **§28 (estado y próximos pasos)** y `CLAUDE.md`. El resto del documento es el plan original; donde difiere, manda el §28.

## 1. Visión

AI World Studio será una aplicación independiente para crear escenarios
3D de mundo abierto, especialmente paisajes de fantasía con montañas,
acantilados, precipicios, mesetas, valles, ríos, lagos, senderos,
bosques y llanuras.

El programa combinará tres métodos: 1. **Esculpido manual:** modificar
el terreno con pinceles y herramientas visuales. 2. **Generación
procedural:** crear formas y características geográficas mediante
parámetros y algoritmos. 3. **Edición con IA:** agentes como Claude
utilizarán las mismas capacidades mediante API y MCP.

El usuario debe poder arrastrar un pincel para levantar una montaña,
hundir un valle, formar una meseta, dibujar un río y generar su cauce,
crear senderos y distribuir vegetación. El agente debe poder hacer esas
mismas operaciones con instrucciones semánticas, sin escribir scripts de
Godot ni editar vértices directamente.

## 2. Decisión fundamental: desarrollo completamente en Python

Todo el código propio del producto se desarrollará en Python: - motor de
terreno; - geometría y procesamiento de mallas; - generación procedural
y erosión; - sistema de features; - biomas y distribución; - API y
MCP; - UI; - validación; - gestión de proyectos; - exportador.

No se desarrollarán módulos propios en C++. Se podrán usar bibliotecas
Python que internamente aprovechen código nativo, pero la aplicación, su
lógica y sus extensiones se mantendrán en Python.

### Stack candidato

  Área                      Tecnología
  ------------------------- ----------------------------------------------
  Lenguaje                  Python 3
  UI                        PySide6
  Datos numéricos           NumPy, SciPy
  Procesamiento de mallas   trimesh, PyMeshLab (evaluar)
  Imágenes y heightmaps     Pillow, imageio
  Formatos 3D               pygltflib, trimesh
  API                       FastAPI
  MCP                       FastMCP
  Datos de proyecto         JSON + SQLite
  Tareas                    asyncio, concurrent.futures, multiprocessing
  Pruebas                   pytest
  Destino                   Godot 4

Las bibliotecas deberán evaluarse por licencia, mantenimiento,
compatibilidad y rendimiento antes de adoptarlas.

### Rendimiento sin C++ propio

-   Usar arrays NumPy y operaciones vectorizadas.
-   Dividir el mundo en chunks.
-   Actualizar solo las regiones modificadas.
-   Separar resolución de edición y resolución de exportación.
-   Cachear resultados intermedios.
-   Ejecutar trabajos pesados fuera del hilo de UI.
-   Usar multiprocessing para tareas independientes.
-   Evitar reconstruir toda la malla en cada movimiento del pincel.

## 3. Arquitectura general

``` text
Agente IA
   |
MCP Server (FastMCP)
   |
World API (FastAPI / servicios Python)
   |
World Orchestrator
   |
+-------------------+--------------------+-------------------+
| Terrain Engine    | Feature Engine     | Biome Engine      |
| Sculpting         | Rivers / Lakes     | Vegetation       |
| Heightfields      | Paths / Cliffs     | Rules / Scatter  |
| Erosion           | Waterfalls         | Density Maps     |
+-------------------+--------------------+-------------------+
   |
Geometry & Material Processing (Python)
   |
World Project (AWP)
   |
Viewport / Inspector / Validator
   |
Godot Exporter
   |
Godot 4
```

La UI y el MCP llamarán a los mismos servicios Python. No debe haber una
implementación distinta para acciones manuales y acciones del agente.

## 4. Representación híbrida del mundo

No representar todo como una única malla ni depender exclusivamente de
un heightmap. El mundo se dividirá en capas independientes.

### 4.1 Terrain Layer

Superficie general mediante heightfields divididos en chunks. Adecuado
para montañas, colinas, llanuras, valles, pendientes y superficies
superiores de mesetas.

Cada chunk tendrá resolución, tamaño físico, datos de altura, normales,
máscaras, límites, LOD y estado de edición.

### 4.2 Feature Layer

Características geográficas paramétricas: - ríos; - lagos; - senderos; -
cascadas; - cauces; - terrazas; - costas; - zonas de transición.

Se conservarán curvas, polígonos, perfiles y parámetros para poder
editarlos posteriormente.

### 4.3 Geometry Layer

Geometría 3D independiente para: - paredes verticales; - acantilados con
salientes; - cuevas; - arcos naturales; - voladizos; - formaciones
rocosas; - puentes naturales; - grandes rocas.

**Limitación técnica:** un heightfield solo almacena una altura por
posición horizontal. No representa correctamente cuevas, arcos ni
superficies que se doblan sobre sí mismas. Esos elementos deben ser
geometría independiente o utilizar otra representación volumétrica.

### 4.4 Biome Layer

Reglas y máscaras para árboles, arbustos, hierba, flores, rocas y
vegetación especializada.

### 4.5 Material Layer

Pasto, tierra, piedra, nieve, arena, barro, roca húmeda y transiciones.
Cambiar un material no debe modificar la geometría.

## 5. Terrain Sculpting Engine

Editor visual de esculpido directo.

### Pinceles

-   Raise: elevar.
-   Lower: hundir.
-   Smooth: suavizar.
-   Flatten: aplanar a una altura objetivo.
-   Level: igualar una región.
-   Stamp: aplicar una forma.
-   Noise: añadir irregularidad.
-   Terrace: crear terrazas.
-   Pinch/Crease: marcar surcos y crestas.
-   Erode: erosionar una zona.
-   Build-up: acumular material.
-   Select/Mask: limitar la edición.

### Parámetros

Radio, intensidad, caída (falloff), dureza, irregularidad, frecuencia,
simetría, acumulación, altura objetivo y máscara de influencia.

### Interacción

-   Pintar arrastrando sobre el terreno.
-   Ajustar radio e intensidad en tiempo real.
-   Undo/redo.
-   Bloquear regiones.
-   Proteger features mediante máscaras.
-   Mostrar el área de influencia.
-   Actualizar solo chunks afectados.

### Formas paramétricas

Crear montaña mediante base, altura, radio y perfil; cordillera mediante
curva; valle mediante trayectoria; meseta mediante polígono y altura;
cuenca para lago; cráter; llanura con transiciones.

Las formas paramétricas deben conservar sus parámetros aunque su
geometría pueda editarse manualmente.

## 6. Procedural Terrain Engine

### Generación

-   Ruido de baja frecuencia para formas generales.
-   Ruido de varias escalas para montañas y colinas.
-   Máscaras regionales.
-   Perfiles de elevación.
-   Cordilleras a partir de curvas.
-   Valles mediante líneas guía.
-   Llanuras con variación controlada.
-   Distribución de pendientes.
-   Semillas reproducibles.

### Erosión

**Hidráulica aproximada:** flujo simplificado, acumulación, canales,
surcos, depósitos y valles de drenaje.

**Térmica:** redistribución de material en pendientes y desprendimientos
aproximados.

**Desgaste geológico:** crestas, estratos, grietas y ruido de detalle.

La primera versión priorizará controles artísticos previsibles y
resultados reproducibles, no una simulación científica completa.

### No destructividad

Cada operación registrará parámetros, semilla, región, capa de entrada,
salida y versión del algoritmo. Permitir reordenar o desactivar
operaciones cuando las dependencias lo permitan.

## 7. Feature Engine: ríos, lagos, caminos y cascadas

Se utilizará un sistema común de curvas con puntos de control,
tangentes, suavizado, anchura variable, elevación, perfiles, proyección
sobre terreno y muestreo de altura.

### Ríos

El usuario dibuja una curva y configura anchura, profundidad, pendiente,
perfil, irregularidad, orillas, material y nivel de agua.

Proceso: 1. Proyectar la curva sobre el terreno. 2. Calcular alturas y
recorrido. 3. Generar cauce. 4. Ajustar el terreno alrededor. 5. Crear
orillas y máscaras. 6. Generar superficie de agua. 7. Validar
continuidad y pendiente.

El agua debe seguir un recorrido topográficamente coherente. Si el río
sube contra la pendiente, el sistema debe advertirlo o proponer
correcciones.

### Lagos

Definir contorno, nivel del agua, profundidad, pendiente de orillas,
irregularidad y conexiones. El sistema crea una cuenca, ajusta las
orillas y valida fugas o terreno visible bajo el agua.

### Senderos

Definir curva, anchura, elevación, pendiente máxima deseada, material,
bordes e irregularidad. El camino debe integrarse con el terreno; no
flotar. Si la pendiente es excesiva, sugerir zigzags, rampas o
escalones.

### Cascadas

Definir origen, destino, altura, anchura, dirección, superficie de
impacto y conexiones. Generar geometría de agua y zona de impacto.
Espuma y partículas avanzadas pueden implementarse en Godot.

## 8. Cliffs & Rock Formation Engine

Esencial para mesetas y precipicios con paredes interesantes.

Funciones: - paredes irregulares; - salientes; - estratos; - grietas; -
columnas; - bloques desprendidos; - cuevas y arcos; - formaciones
aisladas; - transiciones entre suelo y pared.

Métodos: - geometría procedural desde perfiles y curvas; - extrusión de
contornos; - mallas parametrizadas; - deformación con ruido; -
ensamblaje de módulos de roca; - ajuste sobre el terreno; - edición
manual de la malla.

Una meseta se definirá mediante polígono superior, altura, perfil del
borde, forma de paredes, irregularidad, zonas erosionadas, entradas y
rampas. La superficie superior puede usar heightfield; las paredes,
geometría independiente.

Evitar paredes construidas solo con ruido aleatorio: primero debe
existir una silueta y estructura geológica legibles.

## 9. Biome & Vegetation System

### Elementos

Árboles, arbustos, hierba, flores, rocas, troncos, plantas acuáticas y
elementos de suelo. Los modelos podrán importarse desde GLB/glTF y
organizarse en bibliotecas.

### Reglas de distribución

-   altitud;
-   pendiente;
-   orientación;
-   humedad;
-   proximidad al agua;
-   tipo de suelo;
-   bioma;
-   densidad;
-   separación mínima;
-   exposición;
-   zonas prohibidas.

Ejemplos: árboles grandes en pendientes moderadas; rocas y vegetación
escasa en alturas; plantas húmedas cerca de ríos; praderas en
superficies abiertas.

### Herramientas

Paint Biome, Scatter Vegetation, Remove Vegetation, Density Brush,
Exclusion Mask, Slope Filter, Altitude Filter, Random Rotation/Scale,
Cluster Placement y Manual Placement.

### Rendimiento

Agrupar instancias, usar densidad por chunk, LOD, distancias de
visibilidad y culling. Evitar miles de nodos individuales en Godot. El
exportador podrá preparar datos para `MultiMeshInstance3D`.

## 10. Material & Surface System

-   Pintura manual de materiales.
-   Máscaras por altitud, pendiente y humedad.
-   Transiciones y ruido de variación.
-   Capas de materiales.
-   Asignación a formaciones rocosas.
-   Exportación de texturas y máscaras.

Materiales iniciales: grass, dirt, rock, snow, sand, mud, wet rock y
gravel.

La definición de material, parámetros de mezcla y texturas se mantendrán
separados. El shader final puede requerir adaptación al sistema de
materiales de Godot.

## 11. World Inspection & Viewport

El editor debe permitir inspeccionar el mundo desde cualquier
perspectiva, no solo desde arriba.

### Cámaras

-   cámara libre;
-   vista aérea;
-   vista de jugador;
-   vistas ortográficas;
-   puntos panorámicos;
-   recorridos automáticos;
-   cámara para pendientes y siluetas.

### Overlays

Altura, pendiente, normales, wireframe, chunks, límites de biomas,
curvas, máscaras, materiales y densidad de vegetación.

### Inspección por IA

El agente podrá consultar datos, obtener previews, capturar vistas desde
cámaras definidas, inspeccionar regiones, comparar versiones y localizar
zonas uniformes o vacías. La revisión visual es necesaria: no basta con
leer números del heightfield.

## 12. World Validator

### Terreno

-   huecos entre chunks;
-   discontinuidades;
-   normales invertidas;
-   triángulos degenerados;
-   pendientes extremas;
-   resolución insuficiente;
-   cambios abruptos no intencionados.

### Features

-   ríos que suben contra la pendiente;
-   cauces desconectados;
-   lagos con fugas;
-   caminos flotantes o demasiado inclinados;
-   cascadas sin conexión;
-   acantilados separados del terreno;
-   intersecciones no deseadas.

### Vegetación

-   árboles en pendientes imposibles;
-   instancias dentro del agua;
-   solapamientos excesivos;
-   densidad irregular;
-   vegetación en zonas prohibidas.

### Exportación

-   chunks fuera de límites;
-   colisiones ausentes;
-   materiales no encontrados;
-   recursos duplicados;
-   LOD incorrectos;
-   instancias excesivas;
-   referencias rotas.

Cada hallazgo tendrá severidad, ubicación, explicación y corrección
sugerida. La validación automática no reemplaza el juicio artístico.

## 13. Formato de proyecto --- AWP

**AWP (AI World Project)** será el formato editable.

``` text
MyWorld.awp/
├── project.json
├── terrain/
│   ├── chunks/
│   ├── heightfields/
│   ├── masks/
│   └── operations/
├── features/
│   ├── rivers/
│   ├── lakes/
│   ├── paths/
│   ├── waterfalls/
│   └── plateaus/
├── geometry/
│   ├── cliffs/
│   ├── caves/
│   └── rock_formations/
├── biomes/
│   ├── definitions/
│   ├── masks/
│   └── rules/
├── assets/
│   ├── vegetation/
│   ├── rocks/
│   └── props/
├── materials/
├── textures/
├── previews/
├── validation/
├── exports/
└── history/
```

Principios: - conservar fuentes paramétricas; - separar datos editables
de resultados generados; - mantener semillas; - registrar
dependencias; - guardar versiones y checkpoints; - reconstrucción
reproducible; - evitar duplicar assets externos.

Ejemplo: un río depende de su curva y del terreno. Si cambia el terreno
bajo él, marcar el cauce como desactualizado y ofrecer recalcularlo. No
regenerar silenciosamente operaciones que puedan cambiar el diseño.

## 14. API y MCP

El agente debe operar sobre conceptos de paisaje, no editar arrays de
vértices ni escribir scripts de Godot.

### Proyecto y terreno

`create_world`, `load_world`, `save_world`, `get_world_info`,
`get_terrain_info`, `sculpt_terrain`, `raise_terrain`, `lower_terrain`,
`smooth_terrain`, `flatten_terrain`, `create_mountain`,
`create_mountain_range`, `create_valley`, `create_plateau`,
`create_basin`, `apply_terrain_noise`, `apply_erosion`.

### Features

`create_river`, `modify_river`, `create_lake`, `modify_lake`,
`create_path`, `modify_path`, `create_waterfall`, `create_cliff`,
`create_rock_formation`, `create_cave`, `rebuild_feature`.

### Biomas y materiales

`create_biome`, `paint_biome`, `set_biome_rules`, `scatter_vegetation`,
`remove_vegetation`, `paint_terrain_material`, `set_surface_rules`,
`get_biome_info`.

### Inspección y validación

`inspect_terrain`, `inspect_region`, `get_world_preview`,
`capture_world_view`, `analyze_slope`, `analyze_elevation`,
`detect_terrain_issues`, `validate_world`, `compare_world_versions`.

### Optimización y exportación

`build_terrain_chunks`, `generate_lods`, `generate_collisions`,
`optimize_vegetation`, `prepare_godot_export`, `export_to_godot`,
`get_export_status`.

Cada herramienta tendrá parámetros tipados, unidades, defaults,
validaciones, capas afectadas, advertencias, resultados estructurados y
trazabilidad. Las tareas pesadas serán asíncronas con estados `queued`,
`running`, `completed`, `failed` y `cancelled`.

## 15. Flujo autónomo del agente

Ejemplo de solicitud: crear una región montañosa de 2 km², con meseta
central, paredes rocosas, pradera superior, río por un valle hasta un
lago, senderos y miradores.

Flujo: 1. Crear proyecto y definir escala. 2. Generar topografía base.
3. Crear cordilleras y elevaciones. 4. Definir meseta y paredes. 5.
Crear valle. 6. Dibujar río y generar cauce. 7. Crear lago y conexión.
8. Crear senderos con pendientes razonables. 9. Aplicar erosión y
variación geológica. 10. Crear biomas. 11. Distribuir vegetación y
rocas. 12. Aplicar materiales. 13. Capturar vistas aéreas y a nivel de
jugador. 14. Validar geometría y relaciones. 15. Corregir problemas. 16.
Optimizar chunks y vegetación. 17. Exportar a Godot.

### Política de autocorrección

-   checkpoint antes de operaciones grandes;
-   máximo configurable de iteraciones;
-   corregir primero problemas severos;
-   comparar antes/después;
-   rollback si empeora;
-   conservar fuentes paramétricas;
-   informar cambios;
-   pedir revisión ante decisiones artísticas ambiguas.

Modos: Assisted (pide aprobación), Autonomous (ejecuta y valida) y Batch
(procesa tareas y entrega informe).

## 16. Exportación a Godot 4

Godot será el destino de ejecución, no el lugar donde se construye el
terreno.

### Paquete de exportación

``` text
GodotWorld/
├── World.tscn
├── Terrain/
│   ├── Chunk_00_00.glb
│   └── ...
├── Features/
│   ├── Rivers/
│   ├── Lakes/
│   ├── Waterfalls/
│   └── Paths/
├── Geometry/
├── Vegetation/
├── Materials/
├── Textures/
├── Collisions/
├── Navigation/
└── WorldData/
```

### Exportador Python

-   dividir terreno en chunks;
-   generar mallas optimizadas;
-   calcular normales y UVs;
-   generar LOD;
-   crear colisiones;
-   exportar materiales y texturas;
-   preparar agua y features;
-   exportar instancias de vegetación;
-   preservar transformaciones;
-   generar manifiesto;
-   detectar referencias rotas.

### Complemento de Godot

Godot estándar no ejecuta complementos de editor escritos en Python. Por
tanto, el editor y motor propio serán 100% Python, pero el pequeño
adaptador dentro de Godot deberá usar el mecanismo oficialmente
soportado por Godot, normalmente GDScript y `EditorPlugin`.

Ese complemento podrá importar el paquete, organizar recursos,
crear/actualizar escenas, convertir datos de vegetación a
`MultiMeshInstance3D` y configurar colisiones/LOD. Esta excepción es
necesaria para integrarse correctamente con Godot; no introduce C++ ni
cambia el lenguaje del producto principal.

### Rendimiento en Godot

Usar chunks, streaming si el tamaño lo requiere, MultiMeshInstance3D,
colisiones simplificadas, LOD, culling y separación de geometría visible
y de colisión. Validar siempre la escena dentro de Godot.

## 17. Interfaz de usuario

-   **World Viewport:** vista 3D, navegación, pinceles, selección,
    gizmos y overlays.
-   **Terrain Sculpt Panel:** pincel, radio, intensidad, falloff, ruido,
    máscaras y undo/redo.
-   **Feature Editor:** curvas, ríos, lagos, caminos, cascadas, mesetas
    y perfiles.
-   **Biome Editor:** biblioteca de assets, reglas, densidad y filtros.
-   **World Inspector:** chunks, geometría, materiales, dependencias,
    errores y estadísticas.
-   **Agent Activity:** operaciones MCP, tareas, parámetros, previews,
    cambios, errores y rollback.

La UI debe permitir intervención manual sin invalidar innecesariamente
el trabajo del agente.

## 18. Roadmap

Estimación orientativa para un desarrollador experimentado. Depende de
la calidad visual y el rendimiento requerido.

  ------------------------------------------------------------------------
  Fase                                      Duración Entregable
  --------------------- ---------------------------- ---------------------
  1\. Infraestructura y                 3--5 semanas Editor de heightfield
  Sculpting                                          con pinceles

  2\. Procedural                        3--5 semanas Montañas, valles,
  Terrain                                            mesetas y erosión

  3\. Feature Engine                    4--6 semanas Ríos, lagos, caminos
                                                     y cascadas

  4\. Cliffs & Geometry                 3--5 semanas Paredes, acantilados,
                                                     rocas y cuevas
                                                     iniciales

  5\. Biomes &                          3--5 semanas Vegetación y
  Materials                                          materiales
                                                     contextuales

  6\. MCP y autonomía                   3--5 semanas Agente construye,
                                                     inspecciona y corrige

  7\. Godot Exporter                    3--6 semanas Chunks, LOD,
                                                     colisiones,
                                                     vegetación y escenas
  ------------------------------------------------------------------------

Las fases pueden solaparse. No se incluye necesariamente la creación de
una biblioteca artística de árboles, rocas, materiales y efectos.

## 19. Primer entregable recomendado

No empezar con un mundo de varios kilómetros ni con generación
automática completa. Crear una región pequeña que demuestre el flujo
híbrido.

### Escenario de prueba

-   una montaña;
-   una meseta elevada;
-   paredes rocosas;
-   un valle;
-   un río;
-   un lago;
-   un sendero;
-   una pradera;
-   un pequeño bosque;
-   rocas dispersas.

### Flujo de prueba

1.  Crear terreno base.
2.  Esculpir una elevación manualmente.
3.  Generar una cordillera procedural.
4.  Crear una meseta.
5.  Generar paredes rocosas.
6.  Dibujar un río y crear su cauce.
7.  Crear un lago.
8.  Crear un sendero.
9.  Aplicar erosión.
10. Pintar un bioma.
11. Distribuir árboles y rocas.
12. Inspeccionar desde arriba y desde el nivel del jugador.
13. Exportar a Godot.
14. Verificar geometría, materiales y colisiones.

### Criterios de aceptación

-   Esculpido interactivo a resolución de edición.
-   Actualización solo de chunks afectados.
-   Operaciones procedurales reproducibles.
-   Features editables mediante curvas y parámetros.
-   Río adaptado al terreno con cauce real.
-   Lago con cuenca coherente.
-   Camino integrado, sin flotar.
-   Paredes de meseta con geometría real.
-   Vegetación condicionada por altitud y pendiente.
-   Proyecto guardado y reabierto sin perder parámetros.
-   Exportación importable en Godot.

## 20. Estructura del repositorio

``` text
ai-world-studio/
├── app/
│   ├── main.py
│   └── bootstrap.py
├── ui/
│   ├── viewport/
│   ├── panels/
│   ├── tools/
│   └── widgets/
├── core/
│   ├── world/
│   ├── project/
│   ├── tasks/
│   ├── history/
│   └── events/
├── terrain/
│   ├── heightfield/
│   ├── sculpt/
│   ├── procedural/
│   ├── erosion/
│   └── chunks/
├── features/
│   ├── curves/
│   ├── rivers/
│   ├── lakes/
│   ├── paths/
│   ├── waterfalls/
│   └── plateaus/
├── geometry/
│   ├── cliffs/
│   ├── rocks/
│   ├── caves/
│   └── mesh_processing/
├── biomes/
│   ├── rules/
│   ├── vegetation/
│   ├── scatter/
│   └── masks/
├── materials/
├── validation/
├── exporters/godot/
├── api/
├── mcp/
├── schemas/
├── tests/
├── docs/
└── assets/
```

## 21. Pruebas

**Terreno:** elevaciones extremas, límites entre chunks, resolución,
máscaras, filtros, undo/redo y reconstrucción reproducible.

**Features:** curvas abiertas/cerradas, ríos con distintas pendientes,
lagos, caminos, cascadas, cruces y cambios del terreno bajo features.

**Geometría:** normales, UVs, mallas degeneradas, intersecciones,
paredes verticales, salientes y unión con heightfield.

**Biomas:** distribución por altitud/pendiente, proximidad al agua,
exclusiones, densidad y semillas.

**Godot:** importación, materiales, colisiones, LOD, instancias,
escalas, actualización y rendimiento.

## 22. Riesgos y mitigaciones

  -----------------------------------------------------------------------
  Riesgo                              Mitigación
  ----------------------------------- -----------------------------------
  Python lento en mallas grandes      NumPy, vectorización, chunks y
                                      procesamiento por lotes

  Pinceles con latencia               actualización parcial, caché y
                                      resolución de edición

  Heightfield insuficiente            geometría independiente para cuevas
                                      y salientes

  Ríos poco naturales                 curvas, pendiente, flujo y
                                      controles artísticos

  Features desactualizadas            dependencias explícitas y
                                      reconstrucción controlada

  Vegetación costosa                  instancias agrupadas, LOD y culling

  Exportación pesada                  chunks y simplificación

  MCP complejo                        herramientas pequeñas y semánticas

  Agente destruye trabajo             checkpoints, previews e historial

  Terreno repetitivo                  semillas, escalas de ruido y
                                      edición manual

  Integración Godot frágil            manifiesto y pruebas de
                                      reimportación
  -----------------------------------------------------------------------

## 23. Derechos y procedencia

Los modelos de árboles, rocas, plantas, materiales y otros assets deben
conservar información de procedencia y licencia. Importar un asset no
implica permiso para redistribuirlo o incluirlo en un producto
comercial.

Distinguir assets propios, assets con licencia compatible, assets de uso
limitado y assets con licencia no aclarada.

## 24. Relación con AI Garment Studio y AI Motion Studio

Los tres productos serán independientes y especializados: - **AI Garment
Studio:** prendas, patrones, costuras, simulación y fitting. - **AI
Motion Studio:** rigs, poses, locomoción, animación, retargeting y
referencias. - **AI World Studio:** terreno, geografía, biomas,
vegetación y escenarios.

Podrán compartir infraestructura Python: gestión de proyectos, API/MCP
base, tareas asíncronas, historial, checkpoints, importación/exportación
GLB, visor y validación cuando sea viable.

A futuro podrán integrarse en un pipeline de producción, pero ninguno
dependerá de los otros para funcionar.

## 25. Decisiones pendientes

Resolver mediante prototipos (**resueltas en §28.3**): 1. Biblioteca de viewport compatible con
PySide6. 2. Resolución y tamaño de chunks. 3. Representación de
heightfields y máscaras. 4. Algoritmos iniciales de erosión. 5.
Estrategia geométrica para acantilados. 6. Formato interno de curvas y
features. 7. Exportación de vegetación a Godot. 8. Versión exacta de
Godot objetivo. 9. LOD y streaming. 10. Bibliotecas Python que cumplan
rendimiento y licencias.

## 26. Definición de éxito

El usuario podrá esculpir manualmente una región, generar detalles
proceduralmente, dibujar ríos y caminos, crear paredes rocosas reales,
poblar el entorno con biomas y exportarlo a Godot sin reconstruir el
escenario mediante scripts.

Un agente podrá usar esas mismas herramientas para construir una región,
inspeccionarla desde vistas representativas, detectar problemas,
corregirlos y exportar el paquete.

La primera meta no es generar un mundo gigantesco automáticamente: es
crear una región pequeña pero convincente con topografía, relieve, agua,
vegetación y vistas panorámicas.

## 27. Primer sprint

**Objetivo:** demostrar el núcleo de esculpido de terreno en Python.

1.  Crear repositorio y entorno Python.
2.  Integrar PySide6.
3.  Prototipar viewport 3D.
4.  Crear heightfield con NumPy.
5.  Convertir heightfield a malla.
6.  Implementar Raise, Lower, Smooth y Flatten.
7.  Actualizar solo la región afectada.
8.  Implementar cámara libre.
9.  Guardar y cargar terreno.
10. Añadir undo/redo.
11. Medir rendimiento con distintas resoluciones.
12. Documentar límites y decisiones.

**Resultado esperado:** aplicación de escritorio completamente Python
donde el usuario crea terreno, levanta y hunde zonas con pinceles,
suaviza, recorre con cámara libre y guarda el proyecto. Esta base
permitirá incorporar generación procedural, ríos, lagos, acantilados,
biomas y MCP.

## 28. Estado de implementación (11 de noviembre de 2026) y próximos pasos

### 28.1 Enfoque
El producto es el **generador**; Godot es solo un destino. Cada mundo sale como **paquete autocontenido** que se instala en cualquier proyecto de Godot 4
(`World.tscn` solo con el mundo, `Preview.tscn` opcional, rutas `res://` reubicables, perfil `compatible`, marcadores y `Path3D`, navegación horneada),
con terreno propio (chunks GLB) o **Terrain3D**, y agua propia o la del **sistema de agua del juego**. Se verifica siempre con Godot real (importación,
navegación, capturas, jugar con el personaje vía MCP `godot-ai`). Referencia visual: Genshin Impact / Zelda BOTW. Juego del usuario: Bowgame/*Wicktale*
(`~/Desktop/Proyectos/Godot/wicktale` en Windows; antes `prubas-3d` en macOS). Desde 11/2026 se trabaja en Windows (rama `terrain3d-windows`).

### 28.2 Estado por sección
| § | Área | Estado |
|---|---|---|
| 2–3 | Todo en Python; UI/MCP sobre los mismos servicios | **Hecho**: `WorldSession` + `service.py` (56 herramientas); el editor comparte la sesión y la sirve a agentes (puente MCP) |
| 4.1 | Terrain Layer | **Hecho** (heightfield float32, chunks de 64 celdas, LOD; terreno original guardado para repetir el historial) |
| 4.2 | Feature Layer | **Parcial**: ríos con cascadas y poza, lagos, senderos auto-enrutados, montaña escalable, objetos sueltos; **edición posterior** de cualquier operación (`modify_operation`, activar/desactivar). Faltan ríos que se unen y aviso de «desactualizado» |
| 4.3 / 8 | Geometry / acantilados | **Parcial**: muralla de mesetas (malla), terrazas y mesetas con paredes del terreno. Faltan roca esculpida en paredes, arcos, pilares, cuevas |
| 4.4 / 9 | Biomas y vegetación | **Parcial**: dispersión por reglas, objetos colocados a mano, hierba por GPU; se reasientan tras cambios de terreno. Faltan biomas con nombre, flores, pincel de densidad, assets del juego |
| 4.5 / 10 | Materiales | **Parcial**: 8 materiales por reglas + pintura; en Terrain3D, 8 texturas procedurales o el material del juego |
| 5 | Esculpido | **Parcial**: Subir/Bajar/Suavizar/Aplanar/Pintar (registrados como operaciones repetibles). Faltan sello, ruido, terraza, máscaras, simetría |
| 6 | Procedural | **Hecho en lo básico**: ruido, montañas, cordilleras, valles, cuencos, mesetas, terrazas, anillo de montañas, erosión. Presets: `highland_lake`, `mesa_valley`, `river_valley`, `canyon` |
| 7 | Feature Engine | **Parcial** (ver 4.2). Cascadas simuladas columna a columna para el agua del juego |
| 11 | Viewport / inspección | **Hecho en lo esencial**: órbita, vista cenital, capas de análisis (altura, pendiente, agua, transitable), vistas headless para agentes |
| 12 | Validador | **Parcial**: terreno, features, vegetación, exportación, transitabilidad, verificación en Godot (regiones, colisión bajo el inicio, navegación, agua del juego) |
| 13 | AWP | **Parcial**: `project.json` + heightfield + `origin.npy` + `ops.json` + estilo; falta separación por capas y versiones |
| 14 | API / MCP | **Hecho**: 56 herramientas (MCP/REST/CLI), tareas asíncronas, checkpoints, `inspect_godot_project`; con el editor abierto el MCP trabaja sobre su mundo |
| 15 | Flujo autónomo | **Parcial**: el agente construye, mira, valida y corrige; panel «Agente» en vivo. Falta modo asistido (proponer/aprobar) |
| 16 | Exportador Godot | **Hecho en lo esencial**: chunks GLB + LOD + colisión o **Terrain3D** (regiones horneadas con el addon real, material del juego), agua propia o **datos para el agua del juego** (`water.json` + `surface.r32`), vegetación MultiMesh, hierba GPU, navegación Recast, marcadores, manifiesto, validación, reubicación. Faltan streaming, partículas |
| 17 | Interfaz | **Hecho en lo esencial**: diálogo de generación (4 tipos), diálogo de exportación (detecta Terrain3D/material/agua del juego), colocar objetos, editor de curvas, Historial editable, capas de análisis, panel Agente. Faltan biblioteca de assets, pinceles nuevos, modo asistido |
| 21 | Pruebas | **Hecho en lo esencial**: 117 pruebas (Godot real, Terrain3D, editor sin pantalla, puente MCP). Faltan rendimiento y otras versiones de Godot |
| 23 | Procedencia de assets | **No hecho** (todo es procedural y propio) |

### 28.3 Decisiones resueltas (§25)
1. Viewport: `QOpenGLWidget` + PyOpenGL (core 3.3). 2. Chunks de 64 celdas, bordes compartidos. 3. Heightfield float32 + splat maps RGB (8 materiales en 3 texturas).
4. Erosión: térmica e hidráulica aproximada, vectorizadas. 5. Acantilados: mixto (terrazas/mesetas en el heightfield + muralla de malla).
6. Curvas: Catmull-Rom por longitud de arco; edición con asas y caída suave a lo largo de la curva. 7. Vegetación: MultiMesh por especie y chunk + hierba por GPU.
8. Godot objetivo: **4.7.2** (única versión probada); Terrain3D **1.1.0**. 9. LOD: 4 niveles con faldones, sin streaming.
10. Edición no destructiva: se guarda el terreno original y se repite el historial (determinista) al cambiar una operación pasada.
11. Integración con el juego: no se escribe su formato de regiones desde Python (lo hace Godot con su addon en una copia temporal); su agua recibe datos.
12. Librerías: numpy, scipy, Pillow, PySide6, PyOpenGL, FastAPI, mcp 2.x.

### 28.4 Lo construido (resumen por fases; detalle y límites en `docs/sprint1.md`)
- Fases 1–8 (macOS): terreno, procedural, ríos con cascadas, lagos, senderos, montaña escalable, `highland_lake`, exportador portable, navegación, hierba.
- Fase 9: paso a Windows; exportación a **Terrain3D** con el material del juego; verificación y capturas sin abrir el proyecto receptor.
- Fase 10: **agua como datos** para el `water.gd` del juego (nadar/vadear, cascadas toon); probado jugando con el personaje.
- Fase 11: editor — diálogo de exportación, objetos sueltos, edición de curvas y de cualquier operación (repetición del historial), capas de análisis,
  panel Historial, puente editor↔MCP con panel Agente; `water_mask` 60× más rápido.
- Fase 12: presets `mesa_valley`, `river_valley`, `canyon` y selector en el editor; `highland_lake` robusto (20/20 semillas).

### 28.5 Hallazgos técnicos que costó descubrir
Ver `CLAUDE.md` («Lecciones técnicas»): Godot headless no dibuja; Terrain3D (regiones vía addon, `change_region_size` en el árbol, `texture_list` vacía en
juego, fondo infinito del material); agua en terrazas (orillas, cascadas, celdas en el aire); no alfa para pesos; datos exactos en `.tres`; colisión a la
resolución de la malla; copia temporal para todo lo que ejecute Godot; entorno mínimo de los clientes MCP; el editor debe compartir la sesión.

### 28.6 Próximos pasos (orden recomendado)
1. **Acabado visual** (lo que más separa de Genshin; probado con la escena de referencia en `generated_worlds/test_genshin_ref.tscn`):
   a) revestir paredes de mesetas/terrazas y laderas de montaña con piezas de roca 3D (catálogo `rock_cliff` del juego o piezas procedurales) a lo largo
   de los contornos; b) montañas con crestas y erosión en vez de ruido; c) vegetación y pasto con los sistemas del juego (`flora.gd`, `grass_field.gd`, flores).
2. Senderos con id de textura propia; ríos que se unen; cascadas con colisión/sonido.
3. Más presets (costa, alta montaña/glaciar, islas) y formaciones de roca (arcos, pilares, salientes, cuevas).
4. Editor: pinceles nuevos (sello, ruido, terraza, máscaras), biblioteca de assets, modo asistido, dependencias con aviso de «desactualizado».
5. Calidad: validador completo, AWP por capas con versiones, rendimiento en mapas grandes, otras versiones de Godot, procedencia de assets.
6. Integración: fusionar `terrain3d-windows` en `main`; que el usuario recorra los mundos generados con su personaje y ajustar escala/pendientes a su controlador.

------------------------------------------------------------------------

**Fin del plan maestro --- AI World Studio**
