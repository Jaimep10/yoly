# Que Yoly no olvide los datos en Render

En Render el disco normal del servidor se borra en cada deploy, reinicio o cuando el plan gratis
"se duerme". Para que los gastos, ingresos, carpetas y memoria se queden guardados:

1. Render > tu servicio de Yoly > **Disks** > **Add Disk** (necesita plan Starter o superior).
   - Mount Path: `/var/data`
   - Size: 1 GB alcanza de sobra.
2. (Opcional) Render > **Environment**: `DATA_DIR=/var/data`. Si no la pones, Yoly usa `/var/data` sola cuando existe.
3. Deploy. Abre `https://TU-URL.onrender.com/health` y revisa que diga
   `"almacen": {"data_dir": "/var/data", "escribible": true, "persistente": true}`.

Si dice `"persistente": false`, los datos se seguirán borrando en cada reinicio.
