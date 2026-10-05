# kicad-retrata

Acción de GitHub que retrata proyectos KiCad: genera una captura SVG del esquemático y de la placa de la revisión activa de cada módulo, y regenera la tabla de Bill of materials en su documento Markdown. Si algo cambió, hace commit y push.

Nació en [popusintes-esquematicos-placas](https://github.com/piruetasxyz/popusintes-esquematicos-placas) y se separó para reutilizarla en cualquier repositorio KiCad.

## Uso

1. Agregar un `kicad-retrata.yml` en la raíz del repositorio (ver [ejemplos/kicad-retrata.yml](./ejemplos/kicad-retrata.yml)):

   ```yaml
   carpeta-imagenes: docs/images
   modulos:
     - nombre: chufe
       proyecto: chufe/chufe-v-0-rev-a/chufe-v-0-rev-a
       doc: docs/chufe.md
   ```

2. Agregar un workflow en `.github/workflows/` (ver [ejemplos/retratar.yml](./ejemplos/retratar.yml)):

   ```yaml
   permissions:
     contents: write

   jobs:
     retratar:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v7
         - uses: piruetasxyz/kicad-retrata@v0
   ```

3. Si el módulo tiene `doc`, poner en ese archivo las marcas donde va la tabla:

   ```markdown
   <!-- BOM_TABLE_START -->
   <!-- BOM_TABLE_END -->
   ```

4. Mostrar las capturas en el documento, por ejemplo:

   ```markdown
   ![Esquemático de chufe](./images/chufe-esquematico.svg)
   ![Placa de chufe](./images/chufe-placa.svg)
   ```

Para cambiar la revisión activa de un módulo (por ejemplo `rev-a` → `rev-b`) basta con actualizar su `proyecto` en `kicad-retrata.yml`.

## Configuración

Todas las rutas son relativas a la raíz del repositorio.

| Clave | Descripción | Por defecto |
| --- | --- | --- |
| `carpeta-imagenes` | Dónde quedan las capturas: `<nombre>-esquematico.svg` y `<nombre>-placa.svg`. | `docs/images` |
| `capas-placa` | Lista de capas de la captura de la placa. | `[F.Cu, F.Mask, F.Silkscreen, Edge.Cuts]` |
| `descripciones` | Descripciones en español por `lib_id` para la tabla de BOM. Se suman a las que trae la acción (`DESCRIPCIONES_BASE` en [retrata.py](./retrata.py)) y pueden reemplazarlas. | — |
| `modulos` | Lista de módulos (obligatoria). | — |

Cada módulo acepta:

| Clave | Descripción | Por defecto |
| --- | --- | --- |
| `nombre` | Nombre del módulo, usado en los nombres de las capturas (obligatorio). | — |
| `proyecto` | Ruta a la revisión activa, con o sin extensión; se usan el `.kicad_sch` y el `.kicad_pcb` con ese nombre (obligatorio). | — |
| `doc` | Documento Markdown donde se regenera la tabla de BOM. Si no está, no se genera BOM. | — |
| `esquematico` | `false` para no generar la captura del esquemático. | `true` |
| `placa` | `false` para no generar la captura de la placa. | `true` |
| `capas-placa` | Capas propias de este módulo. | las globales |

Si aparece un componente sin descripción, la acción deja un aviso en el resumen del workflow y usa el `lib_id` como descripción.

## Entradas de la acción

| Entrada | Descripción | Por defecto |
| --- | --- | --- |
| `configuracion` | Archivo de configuración. | `kicad-retrata.yml` |
| `version-kicad` | Etiqueta de la imagen de Docker [`kicad/kicad`](https://hub.docker.com/r/kicad/kicad). | `10.0` |
| `carpeta-fuentes` | Carpeta del runner con fuentes que KiCad debe tener instaladas (ver [Fuentes privadas](#fuentes-privadas)). | — |
| `hacer-commit` | `"false"` para solo generar los archivos, sin commit ni push. | `"true"` |
| `mensaje-commit` | Mensaje del commit automático. | `Actualizar capturas y BOM de esquemáticos y placas [skip ci]` |

Salida: `hubo-cambios`, `"true"` si alguna captura o BOM cambió.

## Notas

- **Disparadores**: GitHub no puede leer las rutas desde `kicad-retrata.yml`, así que el workflow escucha `**/*.kicad_sch` y `**/*.kicad_pcb`. Si cambia un archivo que no es de una revisión activa, la acción corre igual pero no hace commit.
- **Fechas en los SVG**: kicad-cli escribe la fecha de exportación dentro de cada SVG. Si lo único que cambió es la fecha, se conserva el SVG anterior para no generar commits vacíos.
- **Hojas jerárquicas**: la captura del esquemático es la de la hoja raíz. La BOM sí incluye todas las hojas, porque kicad-cli aplana la jerarquía.
- **GitHub Pages**: los commits que hace la acción con el `GITHUB_TOKEN` no disparan el evento `push` de otros workflows. Si el sitio se despliega con un workflow propio, hay que dispararlo también con `workflow_run` al terminar este, o desplegar en el mismo workflow después de la acción.
- **Pull requests**: en un pull request no se hace commit (HEAD no está en una rama). Se puede usar `hacer-commit: "false"` y revisar la salida `hubo-cambios`.

## Fuentes privadas

La imagen de Docker de KiCad solo trae fuentes genéricas: si un texto usa una fuente que no está instalada, KiCad la reemplaza sin avisar. Para usar una fuente que no puede quedar pública en el repositorio, se guarda en un secreto y se escribe en una carpeta fuera del repositorio antes de la acción:

1. Guardar la fuente en base64 como secreto del repositorio (Settings → Secrets and variables → Actions), por ejemplo con `gh`:

   ```bash
   base64 -i MiFuente.otf | gh secret set FUENTE_MIFUENTE -R usuario/repositorio
   ```

   Un secreto puede pesar hasta 48 KB, o sea una fuente de unos 36 KB.

2. En el workflow:

   ```yaml
   - name: Instalar fuentes privadas
     env:
       FUENTE_MIFUENTE: ${{ secrets.FUENTE_MIFUENTE }}
     run: |
       mkdir -p "$RUNNER_TEMP/fuentes"
       printf '%s' "$FUENTE_MIFUENTE" | base64 --decode > "$RUNNER_TEMP/fuentes/MiFuente.otf"

   - uses: piruetasxyz/kicad-retrata@v0
     with:
       carpeta-fuentes: ${{ runner.temp }}/fuentes
   ```

Los SVG generados llevan los contornos de las letras usadas (igual que el `render_cache` del `.kicad_pcb`), pero no el archivo de la fuente.

## Uso local

Con KiCad 10 instalado (`kicad-cli` en el PATH), desde la raíz del repositorio KiCad:

```bash
python3 -m venv env
source env/bin/activate
pip install -r ruta/a/kicad-retrata/requirements.txt
python3 ruta/a/kicad-retrata/retrata.py
```

Ojo: las capturas generadas localmente pueden diferir un poco de las de GitHub Actions (por ejemplo, por las fuentes instaladas), así que conviene dejar que las genere la acción.

## Versiones

Se usa [versionado semántico](https://semver.org/lang/es/). Cada versión tiene su etiqueta (`v0.0.1`) y la etiqueta mayor (`v0`) se mueve a la última versión de esa serie, así los repositorios que usan `@v0` reciben los arreglos sin cambiar su workflow.

Para publicar una versión, después de mergear los pull requests, se crea la versión en GitHub con las notas generadas a partir de los pull requests:

```bash
gh release create v0.0.4 -R piruetasxyz/kicad-retrata --target main --generate-notes
```

(o en la web: Releases → Draft a new release → etiqueta nueva → "Generate release notes").

El workflow [mover-etiqueta-mayor.yml](./.github/workflows/mover-etiqueta-mayor.yml) mueve entonces la etiqueta mayor (`v0`) a la versión nueva. Las versiones marcadas como pre-release no la mueven.

## Licencia

[MIT](./LICENSE)
