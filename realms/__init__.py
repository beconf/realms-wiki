# Monkey patch stdlib.
import gevent.monkey
gevent.monkey.patch_all(aggressive=False)

# Set default encoding to UTF-8
import sys

reload(sys)
# noinspection PyUnresolvedReferences
sys.setdefaultencoding('utf-8')

# Silence Sentry and Requests.
import logging
logging.getLogger().setLevel(logging.INFO)
logging.getLogger('raven').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

import time
import sys
import os
import httplib
import traceback
from flask import Flask, request, render_template, url_for, redirect, session, flash, g
from flask.ctx import _AppCtxGlobals
from flask.ext.script import Manager, Server
from flask.ext.login import LoginManager, login_required
from flask.ext.assets import Environment, Bundle
from werkzeug.routing import BaseConverter
from werkzeug.utils import cached_property
from werkzeug.exceptions import HTTPException

from realms import config
from realms.lib.services import db
from realms.lib.ratelimit import get_view_rate_limit, ratelimiter
from realms.lib.session import RedisSessionInterface
from realms.lib.wiki import Wiki
from realms.lib.util import to_canonical, remove_ext, mkdir_safe, gravatar_url, to_dict
from realms.models import User, CurrentUser, Site


sites = {}


class AppCtxGlobals(_AppCtxGlobals):

    @cached_property
    def current_site(self):
        subdomain = format_subdomain(self.current_subdomain)
        if not subdomain:
            subdomain = "www"

        if subdomain is "www" and self.current_subdomain:
            # Invalid sub domain
            return False

        if not sites.get(subdomain):
            sites[subdomain] = to_dict(Site.get_by_name(subdomain))
            sites[subdomain].wiki = Wiki("%s/%s" % (config.REPO_DIR, subdomain))

        return sites[subdomain]

    @cached_property
    def current_wiki(self):
        return g.current_site.wiki

    @cached_property
    def current_subdomain(self):
        host = request.host.split(':')[0]
        return host[:-len(config.DOMAIN)].rstrip('.')

    @cached_property
    def current_user(self):
        return session.get('user') if session.get('user') else {'username': 'Anon'}


class Application(Flask):
    app_ctx_globals_class = AppCtxGlobals

    def __call__(self, environ, start_response):
        path_info = environ.get('PATH_INFO')

        if path_info and len(path_info) > 1 and path_info.endswith('/'):
            environ['PATH_INFO'] = path_info[:-1]

        scheme = environ.get('HTTP_X_SCHEME')

        if scheme:
            environ['wsgi.url_scheme'] = scheme

        real_ip = environ.get('HTTP_X_REAL_IP')

        if real_ip:
            environ['REMOTE_ADDR'] = real_ip

        return super(Application, self).__call__(environ, start_response)

    def discover(self):
        """
        Pattern taken from guildwork.com
        """
        IMPORT_NAME = 'realms.modules'
        FROMLIST = (
            'assets',
            'models',
            'search',
            'perms',
            'broadcasts',
            'commands',
            'notifications',
            'requests',
            'tasks',
            'views',
        )

        start_time = time.time()

        __import__(IMPORT_NAME, fromlist=FROMLIST)

        for module_name in self.config['MODULES']:
            sources = __import__('%s.%s' % (IMPORT_NAME, module_name), fromlist=FROMLIST)

            # Blueprint
            if hasattr(sources, 'views'):
                self.register_blueprint(sources.views.blueprint)

            # Flask-Script
            if hasattr(sources, 'commands'):
                manager.add_command(module_name, sources.commands.manager)

        print >> sys.stderr, ' * Ready in %.2fms' % (1000.0 * (time.time() - start_time))


class RegexConverter(BaseConverter):
    """
    Enables Regex matching on endpoints
    """
    def __init__(self, url_map, *items):
        super(RegexConverter, self).__init__(url_map)
        self.regex = items[0]


def redirect_url(referrer=None):
    if not referrer:
        referrer = request.referrer
    return request.args.get('next') or referrer or url_for('index')


def format_subdomain(s):
    if not config.REPO_ENABLE_SUBDOMAIN:
        return ""
    s = s.lower()
    s = to_canonical(s)
    if s in config.REPO_FORBIDDEN_NAMES:
        # Not allowed
        s = ""
    return s


app = Application(__name__)
app.config.from_object('realms.config')
app.session_interface = RedisSessionInterface()
app.url_map.converters['regex'] = RegexConverter
app.url_map.strict_slashes = False
app.debug = True

# Flask-SQLAlchemy
db.init_app(app)

manager = Manager(app)
manager.add_command("runserver", Server(host="0.0.0.0", port=10000))


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'


@login_manager.user_loader
def load_user(user_id):
    return CurrentUser(user_id)


def error_handler(e):
    try:
        if isinstance(e, HTTPException):
            status_code = e.code
            message = e.description if e.description != type(e).description else None
            tb = None
        else:
            status_code = httplib.INTERNAL_SERVER_ERROR
            message = None
            tb = traceback.format_exc() if g.current_user.staff else None

        if request.is_xhr or request.accept_mimetypes.best in ['application/json', 'text/javascript']:
            response = {
                'message': message,
                'traceback': tb,
            }
        else:
            response = render_template('errors/error.html',
                                       title=httplib.responses[status_code],
                                       status_code=status_code,
                                       message=message,
                                       traceback=tb)
    except HTTPException as e2:
        return error_handler(e2)

    return response, status_code

for status_code in httplib.responses:
    if status_code >= 400:
        app.register_error_handler(status_code, error_handler)

assets = Environment()
assets.init_app(app)
if config.ENV is 'PROD':
    if 'js_common' not in assets._named_bundles:
        assets.register('js_common', Bundle('packed-common.js'))
    if 'js_editor' not in assets._named_bundles:
        assets.register('js_editor', Bundle('packed-editor.js'))
else:
    if 'js_common' not in assets._named_bundles:
        js = Bundle(
            Bundle('vendor/jquery/jquery.js',
                   'vendor/components-underscore/underscore.js',
                   'vendor/components-bootstrap/js/bootstrap.js',
                   'vendor/handlebars/handlebars.js',
                   'vendor/showdown/src/showdown.js',
                   'vendor/showdown/src/extensions/table.js',
                   'js/wmd.js',
                   filters='closure_js'),
            'js/html-sanitizer-minified.js',
            'vendor/highlightjs/highlight.pack.js',
            Bundle('js/main.js', filters='closure_js'),
            output='packed-common.js')
        assets.register('js_common', js)

    if 'js_editor' not in assets._named_bundles:
        js = Bundle('js/ace/ace.js',
                    'js/ace/mode-markdown.js',
                    'vendor/keymaster/keymaster.js',
                    'js/dillinger.js',
                    filters='closure_js', output='packed-editor.js')
        assets.register('js_editor', js)


@app.before_request
def check_subdomain():
    if not g.current_site:
        return redirect('http://%s' % config.DOMAIN)


@app.after_request
def inject_x_rate_headers(response):
    limit = get_view_rate_limit()
    if limit and limit.send_x_headers:
        h = response.headers
        h.add('X-RateLimit-Remaining', str(limit.remaining))
        h.add('X-RateLimit-Limit', str(limit.limit))
        h.add('X-RateLimit-Reset', str(limit.reset))
    return response


@app.template_filter('datetime')
def _jinja2_filter_datetime(ts):
    return time.strftime('%b %d, %Y %I:%M %p', time.localtime(ts))


@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404


@app.route("/")
def root():
    return redirect(url_for(config.ROOT_ENDPOINT))

@app.route("/new/", methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'POST':
        site_name = to_canonical(request.form['name'])

        if Site.get_by_name(site_name):
            flash("Site already exists")
            return redirect(redirect_url())
        else:
            Site.create(name=site_name, founder=g.current_user.id)
            return redirect('http://%s.%s' % (site_name, config.HOSTNAME))
    else:
        return render_template('wiki/new.html')


@app.route("/_account/")
@login_required
def account():
    return render_template('account/index.html')

app.discover()

