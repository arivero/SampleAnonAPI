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

### Ejemplos de uso

Desde el punto de vista del administrador el ciclo deberia ser POST -> GET -> PATCH. Todo se puede ejecutar desde el browser, pero si el CSV necesita retoques puede ser mas comodo ejecutar el POST desde la linea de comandos. 

```
cat <(head -1 Acciones-HIST.csv | sed '1 s/\xEF\xBB\xBF//') <(head -100000 Acciones-PROD.csv | sed '1 s/\xEF\xBB\xBF//') |
time curl -k -X POST "https://localhost/tabla/BiziWithCustSmall"  -H "accept: application/json" 
-H "Authorization: Basic ...." -H "Content-Type: multipart/form-data" 
-F "fileName=@-;type=text/csv" -F "geo=" 
-F "tiempo=DataTimeRemoval" -F "linea=" -F "ids=CustId" -F "ignore=DateTime Arrival"
```

El sistema espera un CSV separado con punto y coma, e incluyendo las cabeceras, dado que se emplean para el parsing inicial: Cuando realiza el post, el administrador puede indicar que columnas son ids personales (los cuales se guardaran pseudomizados), cuales ignorar, cuales corresponden a grados geograficos, y cuales a tiempo. En casi todos los casos se pueden indicar varias columnas, separadas por comas. Hay dos columnas que tienen un papel especial:
 - la primera columna especificada en ```tiempo``` es la que se empleara para la ventana de seleccion de datos basada en inicial e intervalo.
 - la columna que se especifique en ```linea``` actua como identificador unico para el caso en el que se añadan nuevos datos utilizando POST con el mismo nombre de tabla.
 
 Una vez la tabla ha sido enviada, el administrador debe comprobarla -particularmente, que todas las columnas con datos personales han sido hasheadas o ignoradas-usando GET y, si todo esta bien, pasarla a estado _activa_ mediante una llamada a PATCH. En esta llamada puede espeficicar una descripcion, que sera visible para el usuario, y los redondeos "timeDeltas" y "geoDeltas" que se aplicaran a sample,agrega y decala. Los redondeos se indican en segundos, pudiendose aplicar el mismo a las tres llamadas o diferentes, en ese caso se indican separados por comas. 
 
 Una vez activa la tabla, queda a disposicion de los usuarios.
 
 Desde el punto de vista del usuario lo primero deberia ser comprobar que tablas existen mediante una llamada a `/api/info/` y despues posiblemente realizar una llamada a sample, quizas primero con un tanto por uno bastante bajo. Algunas tablas pueden tener demasiadas filas y si la demo no usa SSD (como es el caso de la nuestra) la respuesta puede ser lenta.
 
 Las llamadas permiten un filtrado por ventana temporal, con una especificacion que encontramos conveniente tiempo atras para animaciones:
 - *from* es un parametro que indica la fecha desde la que queremos obtener datos. Su formato es bastante flexible, admitiendo diversas variantes del estandar año-mes-dia-hora-minutos
 - *interval* es, en minutos, la extension de la ventana de la que queremos obtener datos.
 
 ```
 curl -k -X GET "https://193.146.116.108/sample/BiziSimpleHist/?interval=2400&sample=0.01&from=20140202" -H "accept: application/json"
``` 

Sample puede trabajar enviando los datos segun los va cargando, asi que su unico problema es el tiempo. Por contra, la agregacion necesita primero contruir en memoria las series para llamar a numpy, asi que resulta critico garantizar que no haya mas de unas decenas de miles de lineas en la ventana, y excluir todas las columnas que no sean necesarias

```curl -k -X GET "https://193.146.116.108/agregar/TC_ops_idsLinea/?exclude=isImporteCero,ambitoId,ambito,Descripcion,identificadorCompraHash&factor=240000000&from=2016/01/01T10:12&interval=10000" -H "accept: application/json"```

La llamada de agregacion tiene un parametro extra, `factor=`, que permite obtener estadisticas con cestas mayores que la especificada por defecto. En el ejemplo anterior lo hemos exagerado para conseguir simplemente una estadistica global de toda la ventana. Podemos ponerlo mas pequeño

```curl -k -X GET "https://193.146.116.108/agregar/TC_ops_idsLinea/?exclude=isImporteCero,ambitoId,ambito,Descripcion,identificadorCompraHash,lustro&factor=2&from=2016/01/01T10:12&interval=20000" -H "accept: application/json"```

y obtendremos cestos que pueden emplearse para ilustrar un histograma o para fits a distribuciones. El codigo de ejemplo intenta ademas hacer fits de cada cesto individual a varias distribuciones continuas, lo que contribuye a que el tiempo de respuesta pueda ser de entre uno y dos minutos incluso sin fallo de memoria.

