#/user/bin/env python3
# -*- coding:utf-8 -*-

__author__ = 'aiguo Wang'

import asyncio, logging

import aiomysql

def log(sql,args=()):
    logging.info('SQL: %s' % sql)

'''创建连接池'''
#创建一个全局的连接池,每个http请求都可以从连接池中直接获取数据库连接.使用连接池的好处是不必频繁地打开和关闭数据库连接,而是能复用就尽量复用
@asyncio.coroutine
def create_pool(loop=None, **kw):
    logging.info('create database connection pool...')
    global  __pool
    __pool = yield from aiomysql.create_pool(
        host = kw.get('host','localhost'),
        port = kw.get('port',3306),
        user = kw['user'],
        password = kw['password'],
        charset = kw.get('charset','utf8'),
        db = kw["database"],
        # db = kw['db'],
        autocommit = kw.get('autocommit',True),
        maxsize = kw.get('maxsize',10),
        minsize = kw.get('minsize',1),
        loop = loop
    )

'''select'''
@asyncio.coroutine
def select(sql, args, size=None):
    log(sql, args)
    global __pool
    with (yield from __pool) as conn:
        cur = yield from conn.cursor(aiomysql.DictCursor)
        yield from cur.execute(sql.replace('?', '%s'), args or ())
        if size:
            rs = yield from cur.fetchmany(size)
        else:
            rs = yield from cur.fetchall()
        yield from cur.close()
        logging.info('rows returned: %s' % len(rs))
        return rs

'''Insert,Update,Delete'''
@asyncio.coroutine
def execute(sql, args):
    log(sql)
    with (yield from __pool) as conn:
        try:
            cur = yield from conn.cursor()
            yield from cur.execute(sql.replace('?','%s'),args)
            affected = cur.rowcount
            yield from cur.close()
        except BaseException as e:
            raise
        return affected

'''ORM'''
#设计orm需要从上层调用者角度来设计
def creat_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)
class Field(object):
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default
    def __str__(self):
        return '<%s, %s:%s' % (self.__class__.__name__, self.column_type, self.name)

class StringField(Field):
    def __init__(self, name=None, primary_key=False,default=None, ddl='varchar(100)'):
        super().__init__(name,ddl,primary_key,default)

class BooleanField(Field):
    def __init__(self,name=None,default=False):
        super().__init__(name,'boolean',False,default)

class IntegerField(Field):
    def __init__(self,name=None,primary_key=False,default=0):
        super().__init__(name,'biqint',primary_key,default)

class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name,'real',primary_key,default)

class TextField(Field):
    def __init__(self, name=None, default=None):
        super().__init__(name,'text',False,default)

def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)

#映射信息读取出来
class ModelMetaclass(type):
    def __new__(cls, name, bases,attrs):
        #排除model类本身:
        if name=='Model':
            return type.__new__(cls,name,bases,attrs)
        #获取table名称:
        tableName = attrs.get('__table__',None) or name
        logging.info('found model: %s (table: %s)' % (name,tableName))
        #获取所有的Field和主键名
        mappings = dict()
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            if isinstance(v,Field):
                logging.info(' found mapping:%s ==> %s' % (k,v))
                mappings[k] = v
                if v.primary_key:
                    #找到主键:
                    if primaryKey:#判断是否是单一主键
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    fields.append(k)
        if not primaryKey:
            raise RuntimeError('Primary key not found.')
        for k in  mappings.keys():
            attrs.pop(k)
        escaped_fields = list(map(lambda f:'`%s`' % f, fields))
        attrs['__mappings__'] = mappings #保存属性和列的映射关系
        attrs['__table__'] = tableName
        attrs['__primary_key__'] = primaryKey #主键属性名
        attrs['__fields__'] = fields #除主键外的属性名
        # 构造默认的SELECT, INSERT, UPDATE和DELETE语句:
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls,name,bases,attrs)




'''定义model'''
#定义所有orm映射的基类model
class Model(dict, metaclass=ModelMetaclass):
    def __init__(self, **kw):
        super(Model,self).__init__(**kw)
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"' Model' object has no attribute '%s'" % key)
    def __setattr__(self, key, value):
        self[key] = value
    def getValue(self,key):
        return getattr(self,key,None)
    def getValueOrDefault(self,key):
        value = getattr(self,key,None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key,str(value)))
                setattr(self,key,value)
            return value

    @classmethod
    @asyncio.coroutine
    def find(cls,pk):
        'find object by primary key.'
        rs = yield from select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__),[pk],1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        'find objects by where clause.'
        sql = [cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit',None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit,int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit,tuple) and len(limit) == 2:
                sql.append('?,?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value:%s' % str(limit))
        rs = await select(' '.join(sql),args)
        return [cls(**r) for r in rs]

    @classmethod
    @asyncio.coroutine
    def finNumber(cls,selectField, where=None, args=None):
        'find number by select and where'
        sql = ['select % s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = select(''.join(sql),args,1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']


    @asyncio.coroutine
    def save(self):
        args = list(map(self.getValueOrDefault,self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        print(self.__insert__, args)
        rows = yield from execute(self.__insert__,args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

    @asyncio.coroutine
    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

    @asyncio.coroutine
    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)




# class User(Model):
#     __table__ = 'user'
#     id = IntegerField(primary_key=True)
#     name = StringField()
#
#     def show(self):
#         print(1, '__mappings__:', self.__mappings__)
#         print(2, '__table__:', self.__table__)
#         print(3, '__primary_key__:', self.__primary_key__)
#         print(4, '__fields__:', self.__fields__)
#         print(5, '__select__:', self.__select__)
#         print(6, '__insert__:', self.__insert__)
#         print(7, '__update__:', self.__update__)
#         print(8, '__delete__:', self.__delete__)
#
# user = User(id=123, name='Michael',job='eniger')
# print('-------create finish-----------')
# user.show()
# print(9, user)
# user.save()



