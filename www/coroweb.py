
'Web 框架'

import asyncio, os, inspect, logging, functools
from urllib import parse
from aiohttp import web
from apis import APIError

# 将一个函数映射为一个URL处理函数
def get(path):
    '''
    Define decorator @get('/path')
    '''
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        # 加装__method__和__route__属性
        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper
    return decorator

def post(path):
    '''
    Define decorator @post('/path')
    '''
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper
    return decorator

# ---------------------------- 使用inspect模块中的signature方法来获取函数的参数，实现一些复用功能--
# 关于inspect.Parameter 的  kind 类型有5种：
# POSITIONAL_ONLY       只能是位置参数
# POSITIONAL_OR_KEYWORD 可以是位置参数也可以是关键字参数
# VAR_POSITIONAL        相当于是 *args
# KEYWORD_ONLY          关键字参数且提供了key
# VAR_KEYWORD           相当于是 **kw

# 如果url处理函数需要传入关键字参数，且默认是空的话，获取这个key
def get_required_kw_args(fn):
    args = []
    # The Signature object represents the call signature of a callable object and its return annotation
    params = inspect.signature(fn).parameters # parameters 是一个存放函数参数的dict
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)

# 如果url处理函数需要传入关键字参数，获取这个key
def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        # 获取命名关键字参数名
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)

# 判断是否有关键字参数
def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True

# 判断是否有关键字变长参数，VAR_KEYWORD对应**kw
def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True

# 判断是否存在一个参数叫做request，并且该参数要在其他普通的位置参数之后
def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        # 若已经找到"request"关键字,但其不是函数的最后一个参数,将报错
        # request参数必须是最后一个命名参数
        if found and (param.kind != inspect.Parameter.VAR_POSITIONAL and param.kind != inspect.Parameter.KEYWORD_ONLY and param.kind != inspect.Parameter.VAR_KEYWORD):
            raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
    return found

# RequestHandler目的就是从URL处理函数（如handlers.index）中分析其需要接收的参数，从web.request对象中获取必要的参数，
# 调用URL处理函数，然后把结果转换为web.Response对象,以此保证符合aiohttp框架的要求
class RequestHandler(object):

    def __init__(self, app, fn):
        self._app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_arg = has_var_kw_arg(fn)
        self._has_named_kw_args = has_named_kw_args(fn)
        self._named_kw_args = get_named_kw_args(fn)
        self._required_kw_args = get_required_kw_args(fn)

    # 实现了__call__(),其实例可以被视为函数
    # __call__方法的代码逻辑:
    # 1.定义kw对象，用于保存参数
    # 2.判断URL处理函数是否存在参数，如果存在则根据是POST还是GET方法将request请求内容保存到kw
    # 3.如果kw为空(说明request没有请求内容)，则将match_info列表里面的资源映射表赋值给kw；如果不为空则把命名关键字参数的内容给kw
    # 4.完善_has_request_arg和_required_kw_args属性
    async def __call__(self, request):
        kw = None
        # 存在关键字参数/命名关键字参数
        if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
            # http method 为 post的处理
            if request.method == 'POST':
                # request的content_type为空, 返回丢失信息
                if not request.content_type:
                    return web.HTTPBadRequest('Missing Content-Type.')
                ct = request.content_type.lower()   # 获取contnet_type小写字段
                # application/json：消息主体是序列化后的json字符串
                if ct.startswith('application/json'):
                    params = await request.json()   # 以json格式解码
                    # 解码得到的参数不是字典类型, 返回提示信息
                    if not isinstance(params, dict):
                        return web.HTTPBadRequest('JSON body must be object.')
                    kw = params
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    # request.post方法从request读取POST参数,即表单信息,并包装成字典赋给kw变量
                    params = await request.post()
                    kw = dict(**params)
                else:
                    return web.HTTPBadRequest('Unsupported Content-Type: %s' % request.content_type)
            # http method 为 get的处理
            if request.method == 'GET':
                qs = request.query_string   # request.query_string表示url中的查询字符串
                if qs:
                    # 解析query_string,以字典的形如储存到kw变量中
                    kw = dict()
                    for k, v in parse.parse_qs(qs, True).items():
                        kw[k] = v[0]
        # 以上全部不匹配,则获取请求的abstract math_info(抽象数学信息),并以字典形式存入kw
        if kw is None:
            kw = dict(**request.match_info)
        else:
            if not self._has_var_kw_arg and self._named_kw_args:    # not的优先级比and的优先级要高
                # remove all unamed kw:
                copy = dict()
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                kw = copy
            # check named arg:遍历request.match_info, 若其key又存在于kw中,发出重复参数警告
            for k, v in request.match_info.items():
                if k in kw:
                    logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
                kw[k] = v
        # 若存在"request"关键字, 则添加
        if self._has_request_arg:
            kw['request'] = request
        # check required kw:若存在未指定值的命名关键字参数,且参数名未在kw中,返回丢失参数信息
        if self._required_kw_args:
            for name in self._required_kw_args:
                if not name in kw:
                    return web.HTTPBadRequest('Missing argument: %s' % name)
        logging.info('call with args: %s' % str(kw))
        try:
            r = await self._func(**kw)
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)

# os.path.abspath(__file__), 返回当前脚本的绝对路径(包括文件名)
# os.path.dirname(), 去掉文件名,返回目录路径
# os.path.join(), 将分离的各部分组合成一个路径名
# 将本文件同目录下的static目录(即www/static/)加入到应用的路由管理器中
def add_static(app):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))

# 将处理函数注册到app上
# 处理将针对http method 和path进行
def add_route(app, fn):
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    # http method 或 path 路径未知,无法处理
    if path is None or method is None:
        raise ValueError('@get or @post not defined in %s.' % str(fn))
    if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
        fn = asyncio.coroutine(fn)

    # 最后一个参数是形参列表
    logging.info('add route %s %s => %s(%s)' % (method, path, fn.__name__, ', '.join(inspect.signature(fn).parameters.keys())))  
    # 注册request handler
    app.router.add_route(method, path, RequestHandler(app, fn))

# 自动注册所有请求处理函数    
def add_routes(app, module_name):
    n = module_name.rfind('.')
    if n == (-1):
        # __import__ 作用同import语句,
        # __import__('os',globals(),locals(),['path','pip']) ,等价于from os import path, pip
        mod = __import__(module_name, globals(), locals())
    else:
        name = module_name[n+1:]
        # 通过getattr()方法取得子模块名
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    # 遍历模块目录
    for attr in dir(mod):
        # 忽略私有属性和方法
        if attr.startswith('_'):
            continue
        fn = getattr(mod, attr)
        if callable(fn):
            # 获取fn的__method__属性与__route__属性，获得http method与path信息
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            if method and path:
                add_route(app, fn)