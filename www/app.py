'web app主框架'
import logging; logging.basicConfig(level=logging.INFO)# 设置日志等级

import asyncio, os, json, time
from datetime import datetime

from aiohttp import web
from jinja2 import Environment, FileSystemLoader

import orm
from coroweb import add_routes, add_static

from handlers import cookie2user, COOKIE_NAME

# 选择jinja2作为模板, 初始化模板
def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    options = dict(
        autoescape = kw.get('autoescape', True),                        # 自动转义xml/html的特殊字符
        block_start_string = kw.get('block_start_string', '{%'),        # 代码块开始标志
        block_end_string = kw.get('block_end_string', '%}'),            # 代码块结束标志
        variable_start_string = kw.get('variable_start_string', '{{'),  # 变量开始标志
        variable_end_string = kw.get('variable_end_string', '}}'),      # 变量结束标志
        auto_reload = kw.get('auto_reload', True)                       # 每当对模板发起请求,检查模板是否发生改变.若是,则重载模板
    )
    path = kw.get('path', None) # 指定path
    if path is None:
        # 若路径不存在,则将当前目录下的templates(www/templates/)设为jinja2的目录
        # os.path.abspath(__file__), 返回当前脚本的绝对路径(包括文件名)
        # os.path.dirname(), 去掉文件名,返回目录路径
        # os.path.join(), 将分离的各部分组合成一个路径名
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    # 初始化jinja2环境
    # 加载器负责从指定位置加载模板, 选择FileSystemLoader从文件系统加载模板(path已经设置)
    env = Environment(loader=FileSystemLoader(path), **options)
    # 设置过滤器
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items():
            env.filters[name] = f
    app['__templating__'] = env

# 在处理请求之前记录日志
async def logger_factory(app, handler):
    async def logger(request):
        # 记录日志,包括http method, 和path
        logging.info('Request: %s %s' % (request.method, request.path))
        # await asyncio.sleep(0.3)
        return (await handler(request))
    return logger

# 在处理请求之前将cookie解析出来,并将登录信息绑定到request对象上
# 后续的url处理函数可以直接拿到登录用户
# 以后的每个请求,都是在这个middle之后处理的,都已经绑定了用户信息
@asyncio.coroutine
def auth_factory(app, handler):
    @asyncio.coroutine
    def auth(request):
        logging.info('check user: %s %s' % (request.method, request.path))
        request.__user__ = None
        cookie_str = request.cookies.get(COOKIE_NAME)   # 通过cookie名取得加密cookie字符串
        if cookie_str:
            user = yield from cookie2user(cookie_str)   # 验证cookie,并得到用户信息
            if user:
                logging.info('set current user: %s' % user.email)
                request.__user__ = user     # 将用户信息绑定到请求上
        if request.path.startswith('/manage/') and (request.__user__ is None or not request.__user__.admin):
            return web.HTTPFound('/signin')
        return (yield from handler(request))
    return auth

# 解析数据
async def data_factory(app, handler):
    async def parse_data(request):
        if request.method == 'POST':
            # content_type字段表示post的消息主体的类型, 以application/json打头表示消息主体为json
            # request.json方法,读取消息主题,并以utf-8解码
            # 将消息主体存入请求的__data__属性
            if request.content_type.startswith('application/json'):
                request.__data__ = await request.json()
                logging.info('request json: %s' % str(request.__data__))
            # content type字段以application/x-www-form-urlencodeed打头的是浏览器表单
            # request.post方法读取post来的消息主体,即表单信息
            elif request.content_type.startswith('application/x-www-form-urlencoded'):
                request.__data__ = await request.post()
                logging.info('request form: %s' % str(request.__data__))
        return (await handler(request))
    return parse_data

# 将request handler的返回值转换为web.Response对象
async def response_factory(app, handler):
    async def response(request):
        logging.info('Response handler...')
        r = await handler(request)
        # 若响应结果为StreamResponse,直接返回
        if isinstance(r, web.StreamResponse):
            return r
        if isinstance(r, bytes):
            resp = web.Response(body=r)
            resp.content_type = 'application/octet-stream'
            return resp
        # 若响应结果为字节流,则将其作为应答的body部分,并设置响应类型为流型
        if isinstance(r, str):
            # 判断响应结果是否为重定向.若是,则返回重定向的地址
            if r.startswith('redirect:'):
                return web.HTTPFound(r[9:])
            resp = web.Response(body=r.encode('utf-8'))
            # 响应结果不是重定向,则以utf-8对字符串进行编码,作为body.设置相应的响应类型
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        # 若响应结果为字典,则获取它的模板属性
        if isinstance(r, dict):
            template = r.get('__template__')
            # 若不存在对应模板,则将字典调整为json格式返回,并设置响应类型为json
            if template is None:
                resp = web.Response(body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            # 存在对应模板的,则将套用模板,用request handler的结果进行渲染
            else:
                # r['__user__'] = request.__user__  # 增加__user__,前端页面将依次来决定是否显示评论框
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        # 若响应结果为整型的
        # 此时r为状态码,即404,500等
        if isinstance(r, int) and r >= 100 and r < 600:
            return web.Response(r)
        if isinstance(r, tuple) and len(r) == 2:
            # t为http状态码,m为错误描述
            # 判断t是否满足100~600的条件
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # 默认以字符串形式返回响应结果,设置类型为普通文本
        resp = web.Response(body=str(r).encode('utf-8'))
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response

# 时间过滤器
def datetime_filter(t):
    # 定义时间差
    delta = int(time.time() - t)
    # 针对时间分类
    if delta < 60:
        return u'1分钟前'
    if delta < 3600:
        return u'%s分钟前' % (delta // 60)
    if delta < 86400:
        return u'%s小时前' % (delta // 3600)
    if delta < 604800:
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s年%s月%s日' % (dt.year, dt.month, dt.day)

# 初始化
async def init(loop):
    await orm.create_pool(loop=loop, host='127.0.0.1', port=3306, user='www', password='www', db='awesome')
    app = web.Application(loop=loop, middlewares=[
        logger_factory, response_factory
    ])
    init_jinja2(app, filters=dict(datetime=datetime_filter))
    add_routes(app, 'handlers')
    add_static(app)
    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000)
    logging.info('server started at http://127.0.0.1:9000...')
    return srv

loop = asyncio.get_event_loop()
loop.run_until_complete(init(loop))
loop.run_forever()