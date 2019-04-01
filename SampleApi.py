from apispec import APISpec
import falcon
from falcon_swagger_ui import register_swaggerui_app #ojo, necesita version reciente del javascript
from falcon_apispec import FalconPlugin
from falcon_cors import CORS
import sys
import json
import csv
import io
from collections import defaultdict
from hashlib import sha512
from array import array
import secrets
import crypt
#import dateparser #or see https://opensource.com/article/18/4/python-datetime-libraries
import datetime
from dateutil.parser import parse
from ciso8601 import parse_datetime
import pyhash
hasher=pyhash.city_64()
#consider xxhash? https://pypi.org/project/xxhash/  
#Cityhash? Others? https://www.reddit.com/r/programming/comments/700xiv/xxhash_extremely_fast_noncryptographic_hash/
import statistics #or numpy


###### The peewee ORM
from peewee import *
from playhouse.postgres_ext import *

db = PostgresqlExtDatabase('movilidad', user='ayto',server_side_cursors=True) # password='', host='127.0.0.1')

class BaseModel(Model):
    class Meta:
        database = db

class PeeweeConnectionMW(object):
    def process_request(self, req, resp):
        if db.is_closed():
            db.connect()  #o    get_conn()

    def process_response(self, req, resp, resource):
        if resp.stream==None and not db.is_closed():
            db.close()


class Sheet(BaseModel):  
    name = CharField(unique=True)
    estado = CharField() #draft, aprobada, borrada, etc
    fields = ArrayField(CharField,index=False) # convert_values=True ??
    idsKeys = BinaryJSONField(index=False)
    blurDict = BinaryJSONField(index=False)
    info = JSONField(null=True)

class Lines(BaseModel):
    #este modelo puede ser lento al realizar sustituciones de lineas
    hoja = ForeignKeyField(Sheet,backref='lines') 
    fechaBase=TimestampField(index=True)
    lineId=CharField(primary_key=True) #tablename+linenumber. 
    line=BinaryJSONField(index=False) #disable GIN index (es un 30% de carga, podria dejarse opcional)

class UploadLog(BaseModel):
    fecha=DateTimeField(constraints=[SQL("DEFAULT (now())")]) #
    hoja= ForeignKeyField(Sheet,backref='history')
    nlines = IntegerField() ## no hace falta BigInteger, no hay tantas lineas
    options=BinaryJSONField(index=False)
    connectionInfo=BinaryJSONField()  

#
# Solo durante desarrollo: borramos todas las tablas y las reinicializamos
if False:
#if True:
    db.connect()
    db.drop_tables([Lines, UploadLog, Sheet])
    db.create_tables([Lines, UploadLog, Sheet],safe=False)

#
# FALCON
#

#auth stuff
from base64 import b64decode
from hashlib import sha512
sha512hexdigestedAdmin='76ccf93088e27687b85d1ae484610a60614684d6bdcf4999832e07414c01f795f7f2c64538b7289ede467a23a2663a1481f8dec35b4e6280f3d9a707e6cf19f4'
def validateAuth(req, resp, resource, params):
    token = req.get_header('Authorization')
    if token is None:
            description = ('Please provide an auth token '
                           'as part of the request.')
            raise falcon.HTTPUnauthorized('Auth token required', description)
    user,password=b64decode(token.split()[1]).split(b':')
    if sha512(password).hexdigest()!=sha512hexdigestedAdmin:
        description = ('wrong password')
        raise falcon.HTTPUnauthorized('Authentication required',description)
    params['usuario']=str(user,'utf-8')


cors = CORS(allow_all_origins=True)

app=falcon.API(middleware=[PeeweeConnectionMW(),cors.middleware])
    #consider also falcon_cors.CORS with allow_credentials_all_origins etc
spec = APISpec(
    title='API Anonimizada',
    version='0.9.1',
    openapi_version='3.0.0',  #2.0 usa consumes, 3.0 usa requestBody pero no sake muy bien en falcon_swagger_ui
    info=dict(description="Modelo de API para datos anonimizados espacial y temporalmente"),
    plugins=[
        FalconPlugin(app),
    ],
)
spec.components.security_scheme("claveSimple",{"type": "http", "scheme": "basic"})
spec.tag({"name":"admin","description":"necesitan privilegios"})
spec.tag({"name":"csv","description":"fichero en CSV"})
spec.tag({"name":"open","description":"no necesita auth"}) 

### batch tasks, should be suitable for parallelism

def makeBatch(data,t,salt):
    datalot=[]
    conflictset=set()
    idsKeyDict=t.idsKeys
    for linetime,lineid,line in data:
        #print(line,idsKeyDict)
        for k in idsKeyDict:
                    if k in line:
                        if idsKeyDict[k]=="":
                            del line[k]
                        else:
                            line[k]=crypt.crypt(line[k]+salt,idsKeyDict[k])
                            #alternativas:
                            #sha512_crypt.encrypt(line[k]+salt salt=ids[k][1:], rounds=5000)
                            #hashlib...
        if lineid not in conflictset:
          parsedTupleElem=(t,
                   parse_datetime(linetime), #dateparser.parse(linetime),
                   lineid,
                   line)
          datalot.append(parsedTupleElem)
          conflictset.add(lineid) #VALUES no admite varios conflict, usamos solo el primero
    Lines.insert_many(datalot,fields=[Lines.hoja,Lines.fechaBase,Lines.lineId,Lines.line]).on_conflict(
                       conflict_target=Lines.lineId,
                       preserve=[Lines.hoja,Lines.fechaBase,Lines.line]).execute()


#para mover los intervalos temporales, no tengo claro si hasher es overkill
#(y nos bastaria con numericos, https://stackoverflow.com/questions/664014/what-integer-hash-function-are-good-that-accepts-an-integer-hash-key)
#o si es underkill, y tendriamos que usar calidad criptografica
#Para otras ideas, google Monte Carlo generation of monotonic functions
def move(seconds,seed="",multiplo=4096):
    multiplo=4096 #o random basado en seed. En cualquier caso, si es demasiado alto ojo porque 
                  #la escala podria recuperarse por estadistica. Por esto mismo fijamos las seeds posibles
    inferior=(int(seconds ) // multiplo) * multiplo #or secret floor function
    mapinferior=inferior+ (hasher(inferior.to_bytes(4,byteorder=sys.byteorder),seed) % multiplo )- multiplo//2
    superior=inferior+multiplo #or secret ceil function
    mapsuperior=superior+ (hasher(superior.to_bytes(4,byteorder=sys.byteorder),seed) % multiplo) - multiplo//2
    escala=(mapsuperior-mapinferior)/multiplo #or secret interpolator, bezier-like
    seconds=(seconds-inferior)*escala + mapinferior
    return seconds

def moveLatLon(point,seed=""):
  #from geopy.distance import geodesic
  #unimplemented
  #se podria considerar, aparte de ruido aleatorio,
  #el moverlo mediante una version bidimensional de move
  #o con el propio move en la linea de Peano-Hilbert que usa google maps
  pass

#Esta clase es para que falcon mande un aviso una vez ha 
#terminado de mandar el fichero. Posiblemente habria que sustituirlo por algo
#mas async/await en el on_get
class closingMap(map):
  def close(self):
    print("fichero enviado")
    db.close()

#en cualquier caso, usar un map es lento para enviar la respuesta,
#asi que toca usar generadores

import os
def csvPipedStream(cursor,fields):
  k=0
  r,w=os.pipe()
  wfile=open(w,'w',4096*100)
  wr=csv.writer(wfile,delimiter=';')
  rd=open(r,'rb',0)
  wr.writerow(fields)
  for elem in cursor:
    wr.writerow([elem.line.get(x,"NULL") for x in fields]) 
    k=k+1 
    if k % 60 == 0:  #mejor 100 o 200
      wfile.flush() 
      yield os.read(r,4096*100)
      #yield rd.readline()
  db.close()

def csvStream(cursor,fields):
  k=0
  w=io.StringIO() 
  wr=csv.writer(w,delimiter=';')
  wr.writerow(fields)
  for elem in cursor:
    wr.writerow([elem.line.get(x,"NULL") for x in fields]) 
    k=k+1 
    if k % 1200 == 0:  #basta con 120, esto gasta mas memoria
      yield bytes(w.getvalue(),'utf-8')
      w.seek(0)
      w.truncate()
  if k % 1200 != 0:
    yield bytes(w.getvalue(),'utf-8')
  db.close()

@falcon.before(validateAuth)
class Table:
    def on_get(self,req,resp,tabla,usuario):
        """
        ---
        tags:
           - admin
           - csv
        security:
            - claveSimple: []
        summary: Extrae una tabla, neceista privilegios administracion
        parameters:
            - in: path
              name: tabla
              schema:
                  type: string
              description: nombre de la tabla 
        responses:
           '200':
             content:
                text/csv: 
                   schema:
                      type: string
                application/json: 
                   schema:
                      type: object
        """
        if Sheet.select().where(Sheet.name==tabla).exists():
            t=Sheet.get(Sheet.name==tabla)
            ignored=[k for k in  t.idsKeys if t.idsKeys[k]==""]
            fields=[x for x in t.fields if x not in ignored]
            resp.content_type='text/csv'
            query=Lines.select().where(Lines.hoja==t)
            cursor=query.iterator()
            resp.stream=csvStream(cursor,fields) #resp_line
        else:
            resp.body=json.dumps({"NoExiste":tabla})

    #def on_put(self,req,resp,tabla):
    #    """
    #    ---
    #    tags:
    #              - admin
    #               - csv
    #    security:
    #        - claveSimple: []
    #    summary: sustituye con una tabla que sea similar a la producida por GET!!
    #    """
    #    pass

    #patch en general permite cambiar el meta de la tabla sin borrarla
    def on_patch(self,req,resp,tabla,usuario):
        """
        ---
        summary: cambia el estado de una tabla (la activa)
        tags:
            - admin
        security:
            - claveSimple: []
        parameters:
            - in: path
              name: tabla
              schema:
                  type: string
              description: nombre de la tabla 
            - in: query
              name: timeDeltas
              schema:
                  type: string
              description: redondeo de tiempos
            - in: query
              name: geoDeltas
              schema:
                  type: string
              description: redondeo de coordenadas
            - in: query
              name: estado
              schema:
                  type: string
              description: nuevo estado de la tabla (default:activa)
            - in: query
              name: descripcion
              schema:
                  type: string
              description: descripcion de la tabla
            - in: query
              name: familia
              schema:
                  type: string
              description: tabla de la que copia las seeds
        responses:
           '200':
             description: resultado de la operacion
        """
        try:
            t=Sheet.get(Sheet.name==tabla) 
            t.estado="activa" if req.params.get("estado",'')=='' else req.params["estado"]
            info=t.info
            print(req.params)
            if req.params.get("descripcion",'') > '':
              info["descripcion"]=req.params["descripcion"]
            if req.params.get("timeDeltas",'') >'':
              tiempos=[int(x)*60 for x in req.params["timeDeltas"].split(',')]
              if len(tiempos)==1:
                tiempos.append(10*tiempos[0])
              if len(tiempos)==2:
                 tiempos.append(10*tiempos[1])     
              info["timeDeltas"]=tiempos
            if req.params.get("geoDeltas",'') >'':
              tiempos=[int(x) for x in req.params["geoDeltas"].split(',')]
              if len(tiempos)==1:
                tiempos.append(10*tiempos[0])
              if len(tiempos)==2:
                 tiempos.append(2*tiempos[1])     
              info["geoDeltas"]=tiempos 
            info["nlineas"]=t.lines.count() #esto igual habria que ponerlo en subproceso
            t.info=info            
            t.save()
            UploadLog(hoja=t,nlines=-1,connectionInfo={'user':usuario},options={"comando":"activar tabla"}).save()
            resp.body=json.dumps({"Activar":t.name,"info":info})
        except DoesNotExist:
            resp.body=json.dumps({"NoExiste":tabla})
    def on_post(self,req,resp,tabla,usuario):
        """
        ---
        summary: Crea una tabla, especificando tipo de columnas
        tags:
            - admin
            - csv
        security:
            - claveSimple: []
        requestBody:
           content:
              multipart/form-data:
                 schema:
                    type: object
                    properties:
                       fileName:
                          type: file
                          format: binary
                          description: fichero csv
                       geo:
                          type: string
                          description: columnas que especifican geo, para suavizarlas
                       tiempo:
                          type: string
                          description: columna que especifica tiempo
                       linea:
                          type: string
                          description: columna que indica el numero de linea
                       ids:
                          type: string
                          description: columnas que deben ser pseudoanonimizadas
                       ignore:
                          type: string
                          description: columnas que deben ser ignoradas      
        parameters:
            - in: path
              name: tabla
              schema: 
                  type: string
              description: la tabla que se quiere actualizar
            - in: query
              name: salt
              schema:
                  type: string
              description: salt para pseudonimizado

        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """
        print(req.query_string, req.path, req.auth)
        print(req.content_type)
        import cgi
        env = req.env
        env.setdefault('QUERY_STRING', '')
        form = cgi.FieldStorage(fp=req.stream,environ=env) #ojo a https://falcon.readthedocs.io/en/stable/user/faq.html?highlight=multipart
        linecount=0
        salt=req.params.get("salt","") #una opcion es ademas concatenar el usuario que sube la tabla
        blur=dict()
        idsKeyDict=dict()
        for x in form['geo'].value.split(','):
            if len(x) > 0: blur[x]='geo'
        for x in form['tiempo'].value.split(','):
            if len(x) > 0: blur[x]='tiempo'
        for x in form['ids'].value.split(','):
            if len(x) > 0: idsKeyDict[x]=crypt.mksalt(crypt.METHOD_MD5) #unix only? #use secrets.randbits
        for x in form['ignore'].value.split(','):
            if len(x) > 0: idsKeyDict[x]= ""
        numlinea=form['linea'].value
        infoDict=dict()
        infoDict["timeDeltas"]=[12*60,3600,4096] 
        infoDict["geoDeltas"]=[20,20,20]
        infoDict["descripcion"]=""
        #if familia:
        #AltTable=Sheet.get...
        #altInfoDict=altTable.info
        #infoDict["baseSeed"]=altInfoDict["baseSeed"]
        infoDict["baseSeed"]=secrets.token_hex(12)
        Sheet.insert(name=tabla,estado="borrador",fields=dict(),idsKeys=idsKeyDict,blurDict=blur,info=infoDict).on_conflict(
            conflict_target=[Sheet.name],
            preserve=[],#[Sheet.fields,Sheet.idsKeys],
            update={Sheet.blurDict: Sheet.blurDict.concat(blur),},  
            ).execute()

        #oldKeys=Sheet.get(Sheet.name==tabla).idsKeys
        #idsKeyDict.update(oldKeys)
        #Sheet.get(Sheet.name==tabla).update(idsKeys=idsKeyDict).execute()

        t=Sheet.get(Sheet.name==tabla)
        idsKeyDict.update(t.idsKeys)
        t.idsKeys=idsKeyDict
        t.save()

        linetime="1970-01-01 00:00:00.000"

        if len(form['tiempo'].value)>0:
            timefield=form['tiempo'].value.split(',')[0]
        else:
            timefield=None


        if form['fileName'].filename:
            print(form['fileName'].type) #text/csv
            data=[]
            for line in csv.DictReader(io.TextIOWrapper(form['fileName'].file), delimiter=';',
                    quoting=csv.QUOTE_MINIMAL, quotechar='"'):
                linecount = linecount + 1
                if linecount==1: 
                    t.fields=line.keys()
                    t.save()
                    print(line.keys())
                if timefield:
                    linetime=line.get(timefield,linetime)
                bulkTupleElem=( linetime, tabla+str(line.get(numlinea,linecount)), line)
                if linecount < 5: 
                    print(bulkTupleElem)
                data.append(bulkTupleElem)
                if linecount % 10000 ==0 and len(data) > 0 :
                    makeBatch(data,t,salt)
                    data=[]
                    if linecount % 100000 == 0: print (linecount, " lineas ")
            if len(data) > 0:  
                makeBatch(data,t,salt)
        UploadLog(hoja=t,nlines=linecount,connectionInfo={'user':usuario},options=dict((k,form[k].value) for k in form.keys() if k!='fileName')).save()
        resp.body=json.dumps({"numlines":linecount})


    def on_delete(self,req,resp,tabla,usuario):
        """
        ---
        summary: borra una tabla
        tags:
          - admin
        security:
            - claveSimple: []
        parameters:
            - in: path
              name: tabla
              schema: 
                  type: string
              description: la tabla que se quiere actualizar
            - in: query
              name: full
              schema:
                  type: string
              description: si true, borra ademas la info y el log
        responses:
           '200':
             content:
                application/json: 
                   schema:
                      type: object

        """
        print (tabla)
        
        try:
            t=Sheet.get(Sheet.name==tabla)
            #t.lines.delete()
            q=Lines.delete().where(Lines.hoja==t)
            q.execute()
            #t.idsKeys={}
            #t.blurDict={}
            #t.fields=[]
            t.name=t.name+"_DELETED_"+str(t.id)
            t.estado="borrada"
            t.save()
            UploadLog(hoja=t,nlines=-1,connectionInfo={'user':usuario},options={"comando":"delete"}).save()
            resp.body=json.dumps({"savedName":t.name})
        except DoesNotExist:
            resp.body=json.dumps({"NoExiste":tabla})
table_resource=Table()
app.add_route("/tabla/{tabla}",table_resource)
spec.path(resource=table_resource)

class Sample:
    def on_get(self,req,resp,tabla):

        """
        ---
        summary: Muestrea una tabla
        tags:
            - open

        parameters:
            - in: path
              name: tabla
              schema:
                 type: string
            - in: query
              name: interval
              schema:
                 type: integer
              description: muestreo en minutos
            - in: query
              name: sample
              schema:
                 type: string
              description: proporcion de muestreo, tanto por uno
            - in: query
              name: from 
              schema:
                 type: string
              description: fecha de inicio, YYYY/MM/DDTHH:mm
              
        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """
        #pass

        print(tabla)
        sampleFactor=req.params.get('sample','1.0')
        #base=Lines.select(Lines.line).where(Lines.hoja.name==tabla)
        baseTable=Sheet.get(Sheet.name==tabla)
        if baseTable.estado=="activa":
            base=baseTable.lines
            if req.params.get('from','') > '':
                iniciorango=parse(req.params.get('from',''))
                base=base.where(Lines.fechaBase > iniciorango)
                if req.params.get('interval','') > '':
                    finalrango=iniciorango+datetime.timedelta(minutes=int(req.params.get('interval')))
                    base=base.where(Lines.fechaBase<finalrango)

            base=base.where(fn.Random()<sampleFactor).limit(3600)
            response=[]
            for row in base:
                line=row.line
                for x in baseTable.blurDict:
                    if x in line:
                        if baseTable.blurDict[x]=="geo":
                            coordBase=int(float(line[x])*60*60) 
                            coordBase-= coordBase % baseTable.info["geoDeltas"][0]
                            line[x]=coordBase / 3600
                        elif baseTable.blurDict[x]=="tiempo":
                            try:
                                fecha=parse(line[x])
                            except:
                                fecha=parse("1975-01-01 00:00:00.000")
                            fechaBase=fecha.timestamp()
                            fechaBase-= fechaBase % (baseTable.info["timeDeltas"][0]) 
                            line[x]=datetime.datetime.fromtimestamp(fechaBase).isoformat()


                response.append(line)
            resp.body=json.dumps({"data":response,"numlines":len(response)},indent=2)
        else:
            resp.body=json.dumps({"warning":"la tabla existe pero no esta activa"})
sample_resource=Sample()
app.add_route("/sample/{tabla}/",sample_resource)
spec.path(resource=sample_resource)

#la motivacion principal para que exista la agregacion es que tanto el redondeo
#como el desplazamiento alteran los momentos de la distribucion, y el usuario
#de la api puede necesitar esa informacion. Ademas, como resultado extra, se generan
#estadisticas de las columnas adicionales
class Agrega:
    def on_get(self,req,resp,tabla):
        """
        ---
        summary: agrega tabla por intervalos
        tags: 
          - open
        parameters:
            - in: path
              name: tabla
              schema:
                 type: string
            - in: query
              name: interval
              schema:
                 type: integer
              description: longitud del muestreo en minutos
            - in: query
              name: from 
              schema:
                 type: string
              description: fecha de inicio, YYYY/MM/DDTHH:mm
            - in: query
              name: exclude 
              schema:
                 type: string
              description: excluir columnas en esta lista
            - in: query
              name: factor
              schema:
                  type: integer
              description: factor de escala de la agregacion respecto a la predefinida
        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """

        print(tabla)
        #nota: se pueden excluir columnas de indice, ademas de columnas estadisticas
        #TO DO: se podria preparar una llamada con indicacion de binsizes y cosas asi, pero convendria estandarizar
        baseTable=Sheet.get(Sheet.name==tabla)
        if baseTable.estado=="activa":
            base=baseTable.lines
            factor= float(req.params.get('factor','1.0'))
            if factor < 1.0:
              factor=1.0
            exclude= req.params.get('exclude','')
            if type(exclude)=='str':
              exclude=exclude.split(',')
            if req.params.get('from','') > '':
                iniciorango=parse(req.params.get('from',''))
                base=base.where(Lines.fechaBase > iniciorango)
                if req.params.get('interval','') > '':
                    finalrango=iniciorango+datetime.timedelta(minutes=int(req.params.get('interval')))
                    base=base.where(Lines.fechaBase<finalrango)
            #agregacion=defaultdict(lambda: defaultdict(lambda:array('f')))
            import numpy
            import scipy
            import scipy.stats
            totalAgregacion=defaultdict(int)
            agregacion=defaultdict(lambda: defaultdict(lambda:  numpy.empty(0,dtype=numpy.float32)))
            #posiblemente seria mas eficaz hacer la agregacion en la BBDD, creando
            #una tabla temporal con las cestas o simplemente la linekey
            for row in base:
                line=row.line
                linekey=()
                for x in baseTable.blurDict:
                    if x in exclude:
                      continue
                    if x in line:
                        if baseTable.blurDict[x]=="geo":
                            coordBase=int(float(line[x])*60*60) 
                            coordBase-= coordBase % int(baseTable.info["geoDeltas"][1] * factor)
                            line[x]= float(line[x]) # (int(float(line[x])*60*60)-coordBase) / 3600
                            linekey+=(str(coordBase/3600),)
                        elif baseTable.blurDict[x]=="tiempo":
                            try:
                                fecha=parse(line[x])
                            except:
                                fecha=parse("1975-01-01 00:00:00.000")
                            fechaBase=fecha.timestamp()
                            fechaBase-= fechaBase % int(baseTable.info["timeDeltas"][1]*factor) 
                            line[x]= fecha.timestamp()  #-fechaBase 
                            linekey+= (datetime.datetime.fromtimestamp(fechaBase).isoformat(),)
                linekey=",".join(linekey)
                for k , v in line.items():
                  if k in exclude:
                      continue
                  try:
                    valor=float(v)
                    #agregacion[linekey][k].append(valor)
                    agregacion[linekey][k].resize(len(agregacion[linekey][k])+1)
                    agregacion[linekey][k][-1]=valor
                  except ValueError:
                    pass
                totalAgregacion[linekey]+=1
            response={}
            for index,k in agregacion.items():
              response[index]={"total":totalAgregacion[index]}
              for col,serie in k.items():
                res={"count": len(serie),
                    "avg": numpy.mean(serie).item() if len(serie) > 0 else -1,
                    "stdev": numpy.std(serie).item() if len(serie) > 1 else -1,
                    "expon":scipy.stats.expon.fit(serie),
                    "weibull_min":scipy.stats.weibull_min.fit(serie),
                    "pareto":scipy.stats.pareto.fit(serie)
                  }
                response[index][col]=res
            #print (response)
            resp.body=json.dumps({"clavesAgregacion":baseTable.blurDict,"data":response},indent=2)
        else:
            resp.body=json.dumps({"warning":"la tabla existe pero no esta activa"})
    pass
agrega_r=Agrega()
app.add_route("/agregar/{tabla}/",agrega_r)
spec.path(resource=agrega_r)

class Decala:
    def on_get(self,req,resp,tabla):
        """
        ---
        summary: decala datos siguiendo una semilla
        tags:
           - open
        parameters:
            - in: path
              name: tabla
              schema:
                 type: string
            - in: query
              name: interval
              schema:
                 type: integer
              description: muestreo en minutos
            - in: query
              name: seed
              schema:
                 type: string
              description: tabla base cuya semilla se quiere intentar usar para tener tiempos compatibles
            - in: query
              name: from 
              schema:
                 type: string
              description: fecha de inicio, YYYYMMDDHHmm
        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """
        print(tabla)
        #seed=req.params.get('seed','0000')
        #la tabla de referencia puede compatibilizar tanto seed como ruido
        #si la seed de referencia es none entonces no se autoriza el Decalado.
        baseTable=Sheet.get(Sheet.name==tabla)
        if baseTable.estado=="activa":
            #if seed:
            #altTable=Sheet.get(Sheet.name==seed)
            #seed=altTable.info["baseSeed"]
            seed=baseTable.info["baseSeed"]
            interval=baseTable.info["timeDeltas"][2]
            base=baseTable.lines
            if req.params.get('from','') > '':
                iniciorango=parse(req.params.get('from',''))
                base=base.where(Lines.fechaBase > iniciorango)
                if req.params.get('interval','') > '':
                    finalrango=iniciorango+datetime.timedelta(minutes=int(req.params.get('interval')))
                    base=base.where(Lines.fechaBase<finalrango)

            base=base.limit(100000)
            response=[]
            import random #HABRIA QUE USAR HASH PARA REPRODUCIR CADA COORDENADA
            for row in base:
                line=row.line
                for x in baseTable.blurDict:
                    if x in line:
                        if baseTable.blurDict[x]=="geo":
                            coordBase=int(float(line[x])*60*60) 
                            coordBase+= random.randrange(baseTable.info["geoDeltas"][2])
                            line[x]=coordBase / 3600
                        elif baseTable.blurDict[x]=="tiempo":
                            try:
                                fecha=parse(line[x])
                            except:
                                fecha=parse("1975-01-01 00:00:00.000")
                            unixtime=fecha.timestamp()
                            interval=baseTable.info["timeDeltas"][2]
                            fecha=datetime.datetime.fromtimestamp(move(unixtime,seed,interval))
                            line[x]=fecha.isoformat()

                response.append(line)
            resp.body=json.dumps({"data":response,"numlines":len(response)},indent=2)
        else:
            resp.body=json.dumps({"warning":"la tabla existe pero no esta activa"})
decala_r=Decala()
app.add_route("/decalar/{tabla}/",decala_r)
spec.path(resource=decala_r)

class Info:
    def on_get(self,req,resp):
        """
        ---
        tags:
           - open
        description: comandos de informacion (list,log,..) Vease ademas /disponible para la info publica
        responses:
           '200':
                description: resultado de la operacion
        """
        respuesta=[]
        for row in Sheet:
          if not "DELETED" in row.name and row.estado=="activa":
                    respuesta.append({"tabla":row.name, "descr":row.info["descripcion"],
                         "campos":row.fields, "timeDeltas": row.info["timeDeltas"],
                        "redondeos":row.blurDict, "pseudonimizados":[x for x in row.idsKeys]})
        resp.body=json.dumps(respuesta,indent=2)
manage_resource=Info()
app.add_route("/api/info/",manage_resource)
spec.path(resource=manage_resource)

@falcon.before(validateAuth)
class Manage:
    def on_get(self,req,resp,comando,usuario):
        """
        ---
        tags:
           - admin
        security:
            - claveSimple: []
        parameters:
            - in: path
              name: comando
              schema:
                 type: string
                 enum: [list,log]
                 example: list
        description: comandos de informacion (list,log,..) Vease ademas /disponible para la info publica
        responses:
           '200':
                description: resultado de la operacion
        """
        if comando=="list":
            respuesta=[]
            for row in Sheet:
                if not "DELETED" in row.name:
                    respuesta.append({"tabla":row.name,"campos:":row.fields,
                        "redondeos":row.blurDict, "info":row.info, "pseudonimizados":[x for x in row.idsKeys]})
            resp.body=json.dumps(respuesta)
        elif comando=="log":
            respuesta=[]
            for row in UploadLog:
                #if not "DELETED" in row.hoja.name:
                    respuesta.append([row.fecha.isoformat(),row.hoja.name,row.connectionInfo,row.nlines,row.options])
            resp.body=json.dumps(respuesta)
        else:
            print (comando)
manage_resource=Manage()
app.add_route("/api/{comando}/",manage_resource)
spec.path(resource=manage_resource)


#from pprint import pprint
#pprint(spec.to_dict())

class StaticResource(object):
    def on_get(self, req, resp ):
        resp.body=json.dumps(spec.to_dict())
app.add_route('/static/swagger.json', StaticResource())
SWAGGERUI_URL = '/browser'  
register_swaggerui_app(
    app, SWAGGERUI_URL, '/static/swagger.json',
    page_title='Api Anonimizada de Movilidad',
    favicon_url='https://falconframework.org/favicon-32x32.png',
)
