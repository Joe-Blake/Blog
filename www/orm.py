
import asyncio
import logging
import aiomysql

def log(sql, args=()):
    logging.info('SQL: %s' % sql)

# 创建连接池
async def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool                               #连接池由全局变量__pool存储
    __pool = await aiomysql.create_pool(        
        host=kw.get('host', 'localhost'),       # 数据库服务器的位置
        port=kw.get('port', 3306),              # mysql端口
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],                            # 当前数据库名
        charset=kw.get('charset', 'utf8'),      # 连接使用的编码格式为utf-8
        autocommit=kw.get('autocommit', True),  # 自动提交模式,此处默认是False
        maxsize=kw.get('maxsize', 10),          # 最大连接池大小,默认是10,此处设为10
        minsize=kw.get('minsize', 1),           # 最小连接池大小,默认是10,此处设为1,保证了任何时候都有一个数据库连接
        loop=loop                               # 传递消息循环对象loop用于异步执行
    )

# 用于SQL的SELECT语句,sql形参为sql语句,args为填入sql的选项值
# 传入size参数，fetchmany()获取最多指定数量的记录，否则通过fetchall()获取所有记录。
async def select(sql, args, size=None): 
    log(sql, args)
    global __pool
    async with __pool.get() as conn:                                # 从连接池中获取一个数据库连接
        async with conn.cursor(aiomysql.DictCursor) as cur:         # 打开一个DictCursor,以dict形式返回结果
            await cur.execute(sql.replace('?', '%s'), args or ())   # SQL语句的占位符是?，MySQL的占位符是%s
            if size:
                rs = await cur.fetchmany(size)
            else:
                rs = await cur.fetchall()
        logging.info('rows returned: %s' % len(rs))
        return rs

# 用于SQL的INSERT INTO，UPDATE，DELETE语句
async def execute(sql, args, autocommit=True):
    log(sql)
    async with __pool.get() as conn:
        # 若数据库的事务为非自动提交的,则调用协程启动连接
        if not autocommit:
            await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                affected = cur.rowcount
            if not autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                await conn.rollback()
            raise
        return affected

def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)

# 父域
class Field(object):
	def __init__(self,name,column_type,primary_key,default):
		self.name = name
		self.column_type = column_type
		self.primary_key = primary_key
		self.default = default

    # 用于打印信息,依次为类名(域名),属性类型,属性名
	def __str__(self):
		return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)
# 字符串域
class StringField(Field):
    # varchar("variable char"), 可变长度的字符串,100---最长长度
    # ddl定义数据类型
	def __init__(self,name=None, primary_key=False, default=None, ddl='varchar(100)'):
		super().__init__(name, ddl, primary_key, default)

# 布尔域
class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)
 
# 整数域
class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

# 浮点域
class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

# 文本域
class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


# 元类,它定义了如何来构造一个类,任何定义了__metaclass__属性或指定了metaclass的都会通过元类定义的构造方法构造类
# 任何继承自Model的类,都会自动通过ModelMetaclass扫描映射关系,并存储到自身的类属性
class ModelMetaclass(type):

    # cls: 当前准备创建的类对象,相当于self
    # name: 类名
    # bases: 父类的元组
    # attrs: 属性(方法)的字典
    def __new__(cls, name, bases, attrs):
        # 排除Model类本身。Model类主要是用来被继承的,其不存在与数据库表的映射
        if name=='Model':
            return type.__new__(cls, name, bases, attrs)

        #tableName就是在数据库中对应的表名，如果子类中没有定义__table__属性，那默认表名就是类名
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table: %s)' % (name, tableName))
        # 建立映射关系表
        mappings = dict()   # 储存类属性与数据库表的列的映射关系
        fields = []         # 保存除主键外的属性
        primaryKey = None   # 保存主键

        # 遍历类的属性,找出定义的域内的值,建立映射关系
        for k, v in attrs.items():
            if isinstance(v, Field):
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                # 查找并检验主键是否唯一
                if v.primary_key:
                    # 找到主键:
                    if primaryKey:
                        # 主键只能有一个
                        raise StandardError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    # 非主键添加到fields中
                    fields.append(k)
        # 不能无主键
        if not primaryKey:
            raise StandardError('Primary key not found.')
        # 从类属性中删除已加入映射字典的键,避免重名
        for k in mappings.keys():
            attrs.pop(k)
        #字符串两边加上``
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))

        # 创建新的类的属性
        attrs['__mappings__'] = mappings        # 保存属性和列的映射关系
        attrs['__table__'] = tableName          # 保存表名
        attrs['__primary_key__'] = primaryKey   # 保存主键
        attrs['__fields__'] = fields            # 保存非主键属性名

        # 构造默认的select, insert, update, delete语句,使用?作为占位符
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        # 插入数据时,要指定属性名,并对应的填入属性值
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)

# ORM映射基类,继承自dict,通过ModelMetaclass元类来构造类
class Model(dict, metaclass=ModelMetaclass):

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)


    # 增加__getattr__方法,可以"a.b"的形式获取属性
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    # 增加__setattr__方法,可以通过"a.b=c"的形式设置属性
    def __setattr__(self, key, value):
        self[key] = value

    # 取值
    def getValue(self, key):
        return getattr(self, key, None)

    # 取值或取默认值
    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            #当前实例找不到想要的属性值时，在__mappings__属性中查找
            field = self.__mappings__[key]
            if field.default is not None:
                #如果查询出来的字段具有default属性，检查default是方法(返回其调用)还是具体的值(返回值)
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                # 查找到value后将键值对设置为当前对象的属性
                setattr(self, key, value)
        return value


    # 根据主键查找
    @classmethod
    async def find(cls, pk):
        # 直接调用select()方法查询
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        # 在select函数中,打开的是DictCursor,会以dict的形式返回结果
        return cls(**rs[0])

    # findAll---根据Where条件查找；
    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        # 添加默认select语句
        sql = [cls.__select__]
        # 因此若指定有where,在select语句中追加关键字
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        # 追加order by
        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        # 追加limit
        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            # 如果limit为一个整数n，那就将查询结果的前n个结果返回
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            # 如果limit为一个两个值的tuple，则前一个值代表索引，后一个值代表从这个索引开始要取的结果数
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?, ?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = await select(' '.join(sql), args)
        return [cls(**r) for r in rs]

    # 通过where条件查询数量
    @classmethod
    @asyncio.coroutine
    def findNumber(cls, selectField, where=None, args=None):
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = yield from select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']

    # save、update、remove这三个方法需要管理员权限才能操作，所以不定义为类方法，需要创建实例之后才能调用
    async def save(self):
        # 添加非主键属性
        args = list(map(self.getValueOrDefault, self.__fields__))
        # 添加主键
        args.append(self.getValueOrDefault(self.__primary_key__))
        # 执行sql语句后返回影响的结果行数
        rows = await execute(self.__insert__, args)
        # 影响的行数一定为1
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)


