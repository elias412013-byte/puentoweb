# PuenteWeb

Navegar en un iPad viejo (iPad mini 2) usando la PC para cargar las paginas.
La PC abre Edge/Chrome en segundo plano y le manda al iPad la imagen; el iPad manda toques y teclado.

## En la PC
1. Edge o Chrome instalado. Para el cable USB: iTunes (o "Dispositivos Apple").
2. En la carpeta `pc`: `py puente_web.py`

## En el iPad
1. Conectar el cable, desbloquear, tocar "Confiar", abrir PuenteWeb.
2. (Sin cable) la app muestra la IP del iPad: `py puente_web.py --ip ESA_IP`

## Uso
- Tocar = clic, arrastrar = scroll, barra de arriba = direccion o busqueda.
- Botones: atras, adelante, recargar, y escritorio/movil.
- Al tocar un campo de texto aparece el teclado del iPad.
