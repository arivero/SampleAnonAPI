from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
import falcon
from falcon_swagger_ui import register_swaggerui_app #ojo, necesita version reciente del javascript
from falcon_apispec import FalconPlugin
from marshmallow import Schema, fields  #OJO, Marshmallow v3
import json

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
    fields = ArrayField(CharField) # convert_values=True ??
    idsKeys = BinaryJSONField()
    blurDict = BinaryJSONField()  

class Lines(BaseModel):
    name = ForeignKeyField(Sheet,backref='lines')  #index=True?
    lineId=CharField(unique=True) #tablename+linenumber
    line=BinaryJSONField() 

class UploadLog(BaseModel):
    fecha=DateTimeField(constraints=[SQL("DEFAULT (now())")]) #
    name= ForeignKeyField(Sheet,backref='history')
    nlines = IntegerField() ## or BigInteger?
    options=BinaryJSONField()

#
# Solo durante desarrollo: borramos todas las tablas y las reinicializamos

db.connect()
db.drop_tables([Lines, UploadLog, Sheet])
db.create_tables([Lines, UploadLog, Sheet],safe=False)

#
# FALCON
#

app=falcon.API(middleware=[PeeweeConnectionMW()])
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


class Table:
    def on_get(self,req,resp,tabla):
        """
        ---
        tags:
           - admin
        summary: Extrae una tabla, neceista privilegios administracion
        parameters:
            - in: path
              name: tabla
              schema:
                  type: string
              description: nombre de la tabla 
        """
        pass
    def on_put(self,req,resp,tabla):
        """
        ---
        tags:
                   - admin
                   - csv
        summary: sustituye una tabla
        """
        pass
    def on_patch(self,req,resp,tabla):
        """
        ---
        summary: actualiza lineas de una tabla
        tags:
            - admin
        """

    #def on_post()
    def on_post(self,req,resp,tabla):
        """
        ---
        summary: Crea una tabla, especificando tipo de columnas
        tags:
            - admin
            - csv
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
        print(req.query_string, req.path)
        print(req.content_type)
        import cgi
        env = req.env
        env.setdefault('QUERY_STRING', '')
        form = cgi.FieldStorage(fp=req.stream,environ=env) #ojo a https://falcon.readthedocs.io/en/stable/user/faq.html?highlight=multipart
        linecount=0
        if 'salt' in req.params:
            pass
        blur=dict()
        idsKeyDict=dict()
        for x in form['geo'].value.split(','):
            blur[x]='geo'
        for x in form['tiempo'].value.split(','):
            blur[x]='tiempo'
        for x in form['ids'].value.split(','):
            idsKeyDict[x]=123456
        numlinea=form['linea'].value
         
        Sheet.insert(name=tabla,fields=dict(),idsKeys=idsKeyDict,blurDict=blur).on_conflict(
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


        if form['fileName'].filename:
            print(form['fileName'].type)
            for line in form['fileName'].file:
                #print (key,"file")
                linecount = linecount + 1
                if linecount==1: print(line)
                #print(line)
            print (linecount, " lineas")
        resp.body=json.dumps({"numlines":linecount})

    def on_delete(self,req,resp,tabla):
        """
        ---
        summary: borra una tabla
        tags:
          - admin
        """
        pass
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
        pass
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
        description: comandos de gestion de las tablas
        """
        print (comando)
        pass
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
