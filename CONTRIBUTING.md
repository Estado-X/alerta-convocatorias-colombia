# Cómo colaborar con la alerta de convocatorias

Gracias por darte la vuelta. Esta alerta es abierta y gratis, y la lista de páginas crece con lo que propone la gente que busca convocatorias todos los días. No necesitas saber programar para ayudar.

## Lo más útil

1. **Proponer una página que falta.** Abre una [propuesta de página](https://github.com/Estado-X/alerta-convocatorias-colombia/issues/new?template=proponer-pagina.yml).
2. **Avisar de una dirección que se cayó** (da error, cambió de dirección o dejó de mostrar las convocatorias). Abre un [aviso de dirección caída](https://github.com/Estado-X/alerta-convocatorias-colombia/issues/new?template=direccion-caida.yml).
3. **Tomar una tarea para empezar:** las que tienen la etiqueta `good first issue`.
4. **Mejorar el código o la página** con un pull request (abajo).

Necesitas una cuenta gratis de GitHub. **No escribas correos, teléfonos ni datos personales** en los issues: todo lo que se escribe aquí es público.

## Qué página sirve

Una página entra a la lista si cumple todo esto:

- **Es oficial:** de una entidad pública, un fondo, una agencia de cooperación, una universidad o una fundación que publica sus propias convocatorias. No agregadores ni blogs que copian convocatorias de otros.
- **Es una lista de convocatorias, fondos o becas**, no una noticia suelta.
- **Carga sin JavaScript:** el texto de las convocatorias se ve si abres la página con el JavaScript apagado, o si ves el código fuente (Ctrl+U) y ahí aparecen los nombres de las convocatorias. Si la página dice «Cargando…» o sale vacía, la alerta no la puede leer.
- **No pide iniciar sesión** ni bloquea a los robots.
- Sirve a quien busca convocatorias en Colombia (también las del exterior abiertas a colombianos).

Si no sabes si cumple, proponla igual y la revisamos juntos.

## Cómo se revisa una propuesta

1. Probamos la página desde GitHub con `python scripts/revisar.py` en una copia.
2. Si responde y trae texto legible, la agregamos a `fuentes.txt` (o a `fuentes-doctorado.txt`) con su tipo.
3. La primera revisión la guarda como base, sin aviso; desde la siguiente ya avisa cuando aparece algo nuevo.
4. Si no sirve, te contamos por qué en el mismo issue.

## Para quien programa

- El código es Python con la biblioteca estándar (`scripts/revisar.py`) y una página estática (`index.html`, `doctorado/index.html` y `assets/`). Sin dependencias.
- Antes de abrir un pull request, corre las pruebas:

  ```
  python -m unittest discover -s tests
  node --test tests/
  ```

- **No toques `data/`:** la escribe el robot todos los días y un cambio a mano choca con la revisión diaria.
- El formato de `fuentes.txt` tiene que seguir sirviendo a quien ya copió la plantilla: lo nuevo va como columna opcional.
- Los textos que vienen de páginas de terceros se pintan siempre con `textContent`, nunca con `innerHTML`, y en los avisos van dentro de un bloque de código.
- Escribe en español claro, sin jerga. Los commits, también en español.
- Nunca subas claves, tokens ni archivos `.env`.

## Lo que no hacemos a propósito

- Leer, resumir o decir si una convocatoria encaja con un proyecto.
- Sacar fechas de cierre automáticamente: cada página las escribe distinto y un error haría perder una convocatoria.

## Licencia

Lo que aportes queda bajo la [licencia MIT](LICENSE) del repo. Las convocatorias y los datos son de cada entidad.
