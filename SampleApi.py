from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
import falcon
from falcon_swagger_ui import register_swaggerui_app #ojo, necesita version reciente del javascript
from falcon_apispec import FalconPlugin
from marshmallow import Schema, fields  #OJO, Marshmallow v3
from falcon_cors import CORS
import json
import csv
import io
from hashlib import sha512
import crypt
import dateparser

#Si queremos un validador de la query string quizas se podria usar webargs
#from webargs import fields  #https://webargs.readthedocs.io/en/latest/framework_support.html#falcon
#from webargs.falconparser import use_args



###### The peewee ORM
from peewee import *
from playhouse.postgres_ext import *

db = PostgresqlExtDatabase('movilidad', user='ayto') # password='', host='127.0.0.1')

class BaseModel(Model):
    class Meta:
        database = db

class PeeweeConnectionMW(object):
    def process_request(self, req, resp):
        if db.is_closed():
            db.connect()  #o    get_conn()

    def process_response(self, req, resp, resource):
        if not db.is_closed():
            db.close()


class Sheet(BaseModel):
    name = CharField(unique=True)
    estado = CharField() #draft, aprobada, borrada, etc
    fields = ArrayField(CharField) # convert_values=True ??
    idsKeys = BinaryJSONField()
    blurDict = BinaryJSONField()  

class Lines(BaseModel):
    #este modelo puede ser lento al realizar sustituciones de lineas
    hoja = ForeignKeyField(Sheet,backref='lines') #index?
    fechaBase=TimestampField()
    lineId=CharField(unique=True) #tablename+linenumber. 
    line=BinaryJSONField() 

class UploadLog(BaseModel):
    fecha=DateTimeField(constraints=[SQL("DEFAULT (now())")]) #
    hoja= ForeignKeyField(Sheet,backref='history')
    nlines = IntegerField() ## or BigInteger?
    options=BinaryJSONField()
    connectionInfo=BinaryJSONField()  

#
# Solo durante desarrollo: borramos todas las tablas y las reinicializamos
if False:
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
    #print(str(user,'utf-8'),user)


cors = CORS(allow_all_origins=True)

app=falcon.API(middleware=[PeeweeConnectionMW(),cors.middleware])
    #consider also falcon_cors.CORS with allow_credentials_all_origins etc
spec = APISpec(
    title='API Anonimizada',
    version='0.5.0',
    openapi_version='3.0.0',  #2.0 usa consumes, 3.0 usa requestBody pero no sake muy bien en falcon_swagger_ui
    info=dict(description="Modelo de API para datos anonimizados espacial y temporalmente"),
    plugins=[
        FalconPlugin(app),
        MarshmallowPlugin(),
    ],
)
spec.components.security_scheme("claveSimple",{"type": "http", "scheme": "basic"})
spec.tag({"name":"admin","description":"necesitan privilegios"})
#spec.tag({"name":"default","description":"default category"})
spec.tag({"name":"csv","description":"fichero en CSV"})
spec.tag({"name":"open","description":"no necesita auth"})
spec.tag({"name":"stats","description":"condensa o agrega estadisticas"})


#class CategorySchema(Schema):
#    id = fields.Int()
#    name = fields.Str(required=True)

#class PetSchema(Schema):
    #id = fields.Int()
#    category = fields.Nested(CategorySchema, many=True)
#    name = fields.Str()

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
            respuesta=io.StringIO()  #or with as 
            wr=csv.writer(respuesta,delimiter=';')#,quoting=csv.QUOTE_MINIMAL)
            wr.writerow(fields)
            resp.content_type='text/csv'
            for elem in t.lines:
                wr.writerow([elem.line.get(x,"NULL") for x in fields])
            resp.body=respuesta.getvalue()
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
        responses:
           '200':
             description: resultado de la operacion
        """
        try:
            t=Sheet.get(Sheet.name==tabla) 
            t.estado="activa"
            t.save()
            UploadLog(hoja=t,nlines=-1,connectionInfo={'user':usuario},options={"comando":"activar tabla"}).save()
            resp.body=json.dumps({"Activar":t.name})
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
            blur[x]='geo'
        for x in form['tiempo'].value.split(','):
            blur[x]='tiempo'
        for x in form['ids'].value.split(','):
            idsKeyDict[x]=crypt.mksalt(crypt.METHOD_MD5) #unix only? #use secrets.randbits
        for x in form['ignore'].value.split(','):
            idsKeyDict[x]= ""
        numlinea=form['linea'].value
         
        Sheet.insert(name=tabla,estado="borrador",fields=dict(),idsKeys=idsKeyDict,blurDict=blur).on_conflict(
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
                #print (key,"file")
                linecount = linecount + 1
                if linecount==1: 
                    t.fields=line.keys()
                    t.save()
                    print(line.keys())
                if timefield:
                    linetime=line.get(timefield,linetime)
                for k in idsKeyDict:
                    if k in line:
                        if idsKeyDict[k]=="":
                            del line[k]
                        else:
                            line[k]=crypt.crypt(line[k]+salt,idsKeyDict[k])
                            #alternativas:
                            #sha512_crypt.encrypt(line[k]+salt salt=ids[k][1:], rounds=5000)
                            #hashlib...

                bulkTupleElem=(t,
                    dateparser.parse(linetime),
                    tabla+str(line.get(numlinea,linecount)),
                    line)
                if linecount < 5: 
                    print(bulkTupleElem)
                data.append(bulkTupleElem)
            print (linecount, " lineas ",len(data))
            Lines.insert_many(data,fields=[Lines.hoja,Lines.fechaBase,Lines.lineId,Lines.line]).on_conflict(
                conflict_target=Lines.lineId,
                preserve=[Lines.hoja,Lines.fechaBase,Lines.line]).execute()
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
            Lines.delete().where(Lines.hoja==t)
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
              name: format
              schema:
                 type: string
              description: formato de salida, json o csv quizas
            - in: query
              name: from 
              schema:
                 type: string
              description: fecha de inicio, YYYYMMDDHHMM
              
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
            base=base.where(fn.Random()<sampleFactor)
            response=[row.line for row in base] 
            resp.body=json.dumps({"data":response,"numlines":len(response)})
        else:
            resp.body=json.dumps({"warning":"la tabla existe pero no esta activa"})
sample_resource=Sample()
app.add_route("/sample/{tabla}/",sample_resource)
spec.path(resource=sample_resource)

class Agrega:
    def on_get(self,req,resp,tabla):
        """
        ---
        summary: agrega tabla por intervalos
        tags: 
          - open
          - stats
        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """
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
        responses:
           '200':
             description: resultado de la operacion
             content:
                application/json: 
                   schema:
                      type: object
        """
    pass
decala_r=Decala()
app.add_route("/decalar/{tabla}/",decala_r)
spec.path(resource=decala_r)

class Manage:
    def on_get(self,req,resp,comando):
        """
        ---
        tags:
           - open
           - stats
        parameters:
            - in: path
              name: comando
              schema:
                 type: string
                 enum: [list,log]
                 example: list
        description: comandos de informacion (list,log,..)
        responses:
           '200':
                description: resultado de la operacion
        """
        if comando=="list":
            respuesta=[]
            for row in Sheet:
                if not "DELETED" in row.name:
                    respuesta.append({"tabla":row.name,"campos:":row.fields,
                        "redondeos":row.blurDict, "pseudonimizados":[x for x in row.idsKeys]})
            resp.body=json.dumps(respuesta)
        elif comando=="log":
            respuesta=[]
            for row in UploadLog:
                if not "DELETED" in row.hoja.name:
                    respuesta.append([row.fecha.isoformat(),row.hoja.name,row.connectionInfo,row.nlines,row.options])
            resp.body=json.dumps(respuesta)
        else:
            print (comando)
manage_resource=Manage()
app.add_route("/api/{comando}/",manage_resource)
spec.path(resource=manage_resource)


from pprint import pprint
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
    #config hay que modificarla para poner los campos de OAuth
    #config={'supportedSubmitMehods': ['get','post','put'], }
)


#### COMENTARIOS FINALES, etc
#async: aunque sea WSGI, falcon es bastante rapido, asi que seguramente no hace falta considear ASGI aun. 
