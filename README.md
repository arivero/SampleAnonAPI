## Anon API

Este es un prototipo de una API para gestion de datos anonimizados, orientado a datos con
localizacion cronologica o geografica. A fecha de Abril de 2019, hay una demo corriendo en https://193.146.116.108/browser 

La API consiste basicamente de una parte administrativa que admite la subida y descarga de tablas CSV, y una parte publica
donde los datos se suministran en json.

La parte publica de la API considera tres tipos de llamadas:

- sample, donde se recogen una muestra de una tabla. Los datos espaciales y temporales se entregan redondeados a la escala
que haya especificado el administrador

- agrega, donde se pueden recoger informacion estadistica de los conjuntos de datos de cada zona.

- decala, donde se pueden recoger datos temporales sin redondear pero decalados una cantidad aleatoria, pero
garantizando la cronologia.

La parte privada de la API permite a un administrador gestionar las tablas, en particular

- POST permite enviar una tabla a la vez que se indica que columnas son datos personales, para pseudonimizar o directamente 
ignorar, y que columnas son datos temporales o espaciales. 

- GET permite recojer en CSV exactamente la tabla que ha sido guardada en el servidor. De los datos pseudonimizados solo se guarda
su hash, mientras que el resto de los datos se guardan con la precision original.

- DELETE permite eliminar la tabla

- PATCH activa la tabla para que sea visible al publico, y permite alterar algunos metadatos: descripcion de la tabla, y redondeos a aplicar en cada fase

Ademas, existen tres comandos para obtener informacion general. Un administrador puede usar `list` para consultar todas las tablas incorporadas al sistema y `log` para obtener informacion de la actividad. 
Un usuario cualquiera puede obter informacion de las tablas activas usando `info`, por ejemplo desde la linea de comandos:

```
curl -k -X GET "https://193.146.116.108/api/info/" -H "accept: */*"
```

### Instalacion

Las dependencias son las que se ven en las cabeceras: peewee, falcon, etcetera. La Base de Datos que estamos usando es Postgresql,
pero practicamente todas las llamadas son via peewee, lo que permitiria poner el modelo en otro tipo de base de datos. La estructura
es tambien bastante agnostica, de forma que seria posible tener persistencia via alguna bbdd NoSQL.

Ejecuta en desarrollo usando

para https:
```
sudo PYTHONUNBUFFERED=1 gunicorn3 --timeout 360000 --certfile=/etc/ssl/certs/ssl-cert-snakeoil.pem --keyfile=/etc/ssl/private/ssl-cert-snakeoil.key -R --capture-output --log-level=DEBUG   -b 0.0.0.0:443 --reload SampleApi:app 2>&1  | tee -a gunicorn443New.lo
```

No es recomendable usar http dado que la clave de administrador esta definida en modo Basic y por tanto viaja en texto plano.

La clave en si misma esta fijada, para poner una nueva hay que calcular
```
sha512("mi_nueva_clave".encode("utf-8")).hexdigest()
```
Y poner el resultado como valor de la variable `sha512hexdigestedAdmin`


