# El Ritual Secreto de la Puerta Plateada

Landing de Halloween de Alpina con su servidor de códigos, sellos, premios y sorteo.
Todo corre con Python 3.9 o superior y no necesita instalar nada más.

```
alpina-ritual-puerta-plateada/
├── public/            la landing (index.html e imágenes)
├── server.py          web + API + panel de administración + herramientas
├── Dockerfile         receta para publicarlo en la nube
├── railway.json       configuración de Railway
└── data/ritual.db     base de datos SQLite local (se crea sola al arrancar)
```

## Cómo funciona la mecánica

1. Cada tapa trae un **código único** de 8 caracteres y un **QR** con ese mismo código.
2. El QR abre `https://SU-DOMINIO/?tapa=bonyurt&c=CODIGO`. El niño despega la Puerta Plateada, el servidor valida el código y el sello queda en su pasaporte.
3. Si el QR falla, el niño escribe el código a mano en la sección de sellos.
4. Cada código sirve **una sola vez**. Si el niño ya tenía ese sello, el código no se gasta y le sirve a otra persona.
5. Con los **3 sellos** se desbloquea el **premio seguro**: el filtro AR secreto y el adelanto de la serie. Los enlaces solo los entrega el servidor a quien completó el pasaporte, así que no aparecen en el código de la página.
6. Un **adulto** inscribe al niño en el **sorteo** del kit PR premium. El servidor solo acepta la inscripción si ese participante tiene los 3 sellos.

El participante se identifica con un número anónimo guardado en su navegador. Los datos personales solo existen si un adulto llena la inscripción.

## Probar en tu computador

```bash
cd alpina-ritual-puerta-plateada
python3 server.py generate --product bonyurt   --count 5 --batch prueba
python3 server.py generate --product alpinette --count 5 --batch prueba
python3 server.py generate --product yogoyogo  --count 5 --batch prueba
python3 server.py serve --port 8000
```

Abre `http://127.0.0.1:8000` y usa los códigos de los CSV que se generaron.
Para simular un QR, abre una de las URL de la columna `url_qr` cambiando el dominio por `http://127.0.0.1:8000`.

Si abres `public/index.html` con doble clic, la página corre en **modo demo**: acepta cualquier código de 8 caracteres y no guarda nada en el servidor.

## Generar los códigos para impresión

Para la campaña real, crea los códigos en el servidor publicado como explica la Parte 4 de la guía de publicación. El comando de abajo solo llena la base de datos local.

```bash
export RITUAL_SITE_URL=https://SU-DOMINIO.com
python3 server.py generate --product bonyurt --count 200000 --batch lote1
```

Cada comando crea un CSV con `codigo, producto, lote, url_qr`. Ese archivo se entrega a la imprenta para la impresión de dato variable: el código va impreso en la tapa y el QR se genera desde `url_qr`.
Los códigos no usan 0, O, 1 ni I para que los niños no se confundan.

**Importante:** los CSV y la base de datos son secretos. Quien los tenga puede canjear sellos.

## Variables de configuración

| Variable | Para qué sirve |
|---|---|
| `RITUAL_SITE_URL` | Dominio público. Se usa en las URL de los QR. |
| `RITUAL_ADMIN_TOKEN` | Clave del panel de administración. Sin ella el panel queda apagado. |
| `RITUAL_SECRET_FILTER_URL` | Enlace del filtro AR secreto "Modo Guardián". |
| `RITUAL_SERIES_PREVIEW_URL` | Enlace del adelanto exclusivo de la serie. |
| `RITUAL_DB` | Ruta de la base de datos. En Railway se usa sola la carpeta del volumen. En local es `data/ritual.db`. |
| `RITUAL_TRUST_PROXY` | Pon `1` si el servidor está detrás de un proxy. El `Dockerfile` ya lo trae activado. |
| `RITUAL_POLICY_VERSION` | Versión de la política de datos que acepta el adulto. Queda guardada con cada inscripción. |

Mientras los enlaces del premio estén vacíos, los botones dicen "Se activa muy pronto".

Las fechas de la campaña están en dos lugares y deben coincidir: `START` y `END` en `server.py`, y `CONFIG.start` y `CONFIG.deadline` en `public/index.html`. Hoy van del 1 al 31 de octubre de 2026, hora de Colombia. En el mismo bloque `CONFIG` están los enlaces a redes y la fecha del sorteo.

## Publicarlo con una URL pública

La ruta recomendada es **GitHub + Railway**. No necesita instalar nada en el computador: todo se hace desde el navegador.
Railway da HTTPS y una dirección pública, y guarda la base de datos en un volumen que no se borra al actualizar.
Railway es un servicio de pago. Revisa el precio vigente del plan Hobby en railway.com antes de empezar.

El proyecto ya trae lo que Railway necesita: `Dockerfile`, `railway.json` y el chequeo de salud en `/healthz`.

### Parte 1 · Subir el proyecto a GitHub

1. Crea una cuenta en https://github.com si no tienes una.
2. Arriba a la derecha, pulsa **+** y luego **New repository**.
3. Ponle de nombre `ritual-puerta-plateada`, márcalo como **Private** y pulsa **Create repository**.
4. En la página del repositorio vacío, pulsa el enlace **uploading an existing file**.
5. Abre en Finder la carpeta `alpina-ritual-puerta-plateada` y arrastra a la página estos elementos: la carpeta `public`, `server.py`, `Dockerfile`, `railway.json` y `README.md`.
6. Abajo, pulsa **Commit changes** y espera a que termine.

Nunca subas archivos `.csv` ni `.db`. Tienen códigos o datos personales.

### Parte 2 · Publicar en Railway

1. Entra a https://railway.com y pulsa **Login** con tu cuenta de GitHub.
2. Pulsa **New Project**, luego **Deploy from GitHub repo**. Autoriza el acceso si lo pide y elige `ritual-puerta-plateada`.
3. Railway detecta el `Dockerfile` y construye el proyecto. Espera a que el servicio diga **Active** o **Success**.
4. **Conecta el volumen antes de cualquier otra cosa.** Haz clic derecho sobre el servicio, elige **Attach volume** y pon como ruta de montaje `/data`.
5. Abre la pestaña **Variables** del servicio y agrega:
   - `RITUAL_ADMIN_TOKEN`: una clave larga. Puedes crearla en la Terminal del Mac con `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - `RITUAL_SECRET_FILTER_URL` y `RITUAL_SERIES_PREVIEW_URL`: los enlaces del premio seguro, cuando los tengas.
6. Ve a **Settings**, sección **Networking**, y pulsa **Generate Domain**. Si te pide un puerto, usa el que aparece en los registros (**Deploy Logs**) en la línea `El ritual está abierto en http://0.0.0.0:PUERTO`.
7. Copia la dirección que te da, algo como `https://ritual-puerta-plateada-production.up.railway.app`.
8. Vuelve a **Variables** y agrega `RITUAL_SITE_URL` con esa dirección, sin barra al final. Railway reinicia el servicio solo.

### Parte 3 · Comprobar que quedó bien

1. Abre tu dirección en el celular. Debe aparecer la Puerta Plateada.
2. Abre `TU-DIRECCION/healthz`. Debe responder `{"ok": true}`.
3. En **Deploy Logs** debe aparecer `Base de datos: /data/ritual.db` y **no** debe aparecer ninguna línea que empiece por `ALERTA`. Si aparece, el volumen no está conectado y los datos se perderían.

### Parte 4 · Crear los códigos para las tapas

Los códigos se crean en el servidor publicado, porque deben quedar en su base de datos. Desde la Terminal del Mac:

```bash
curl -X POST -H "Authorization: Bearer TU-CLAVE" \
  "https://TU-DIRECCION/admin/generate?product=bonyurt&count=1000&batch=lote1" -o codigos-bonyurt.csv
```

Repite cambiando `bonyurt` por `alpinette` y `yogoyogo`. Cada llamada admite hasta 500.000 códigos.
Entrega esos CSV a la imprenta. La columna `url_qr` es la que va dentro del QR de cada tapa.

**Decide el dominio definitivo antes de imprimir.** Los QR llevan la dirección escrita. Si después cambian a otro dominio, las tapas ya impresas apuntarían al anterior.
Para usar un dominio propio, como `ritual.alpina.com`, ve a **Settings**, sección **Networking**, pulsa **Custom Domain** y sigue las instrucciones de DNS que muestra Railway. Luego actualiza `RITUAL_SITE_URL` y genera los códigos.

### Actualizar la página más adelante

Sube a GitHub el archivo que cambió, con el mismo método de la Parte 1. Railway lo detecta y vuelve a publicar solo.
Los sellos, códigos e inscripciones siguen intactos porque viven en el volumen.

### Copias de seguridad

Descarga los inscritos con frecuencia con el comando de `registrations.csv` del panel. Railway también permite programar copias del volumen desde su pestaña **Backups**.

### Otras plataformas

El `Dockerfile` funciona en cualquier servicio que ejecute contenedores con disco persistente, como Fly.io o un servidor propio. Lo único obligatorio es que `RITUAL_DB` apunte a un disco que no se borre.

## Panel de administración

Todas las rutas piden la clave en el encabezado `Authorization: Bearer CLAVE`.

```bash
curl -X POST -H "Authorization: Bearer CLAVE" "https://SU-DOMINIO.com/admin/generate?product=bonyurt&count=1000&batch=lote1" -o codigos.csv
curl -H "Authorization: Bearer CLAVE" https://SU-DOMINIO.com/admin/stats
curl -H "Authorization: Bearer CLAVE" https://SU-DOMINIO.com/admin/registrations.csv -o inscritos.csv
curl -X POST -H "Authorization: Bearer CLAVE" "https://SU-DOMINIO.com/admin/draw?n=3"
```

Lo mismo funciona desde la consola del servidor:

```bash
python3 server.py stats
python3 server.py export --out inscritos.csv
python3 server.py draw --n 3
```

El sorteo usa un generador aleatorio criptográfico y guarda a los ganadores para que nadie salga dos veces.
El CSV abre bien en Excel con tildes y está protegido contra fórmulas maliciosas en los nombres.

## Protección incluida

- Freno a quien intenta adivinar códigos: máximo 10 códigos fallidos cada 15 minutos por participante y 20 por conexión.
- Límite general de 240 peticiones por minuto por conexión.
- Cada código se canjea de forma atómica, así que dos personas no pueden usar el mismo al tiempo.
- Fuera de las fechas de campaña el servidor no acepta códigos.
- Máximo 5 inscripciones por correo, para que un adulto pueda inscribir hermanos sin abrir la puerta al abuso.
- Encabezados de seguridad, sin listado de carpetas y sin exponer archivos fuera de `public/`.

## Capacidad

Este servidor aguanta bien una campaña con tráfico moderado. Si esperan picos muy grandes, por ejemplo por pauta en televisión, conviene servir `public/` desde una CDN y medir la API antes del lanzamiento.

## Pendientes antes de salir al aire

- Autorización de **Coljuegos** para el sorteo y su número en el texto legal del pie de página.
- Términos y condiciones, y política de tratamiento de datos con su versión.
- Enlaces del filtro AR secreto y del adelanto de la serie.
- Fecha del sorteo en `CONFIG.drawDate`.
- Enlace de YouTube para "Los Secretos de Sopó".
