from flask import g, render_template, request, redirect, Blueprint, flash, url_for
from flask.ext.login import login_required
from realms import app, redirect_url, config
from realms.lib.util import to_canonical, remove_ext
from realms.lib.wiki import Wiki
from realms.models import Site

blueprint = Blueprint('wiki', __name__)


@blueprint.route("/wiki/new/", methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'POST':
        wiki_name = to_canonical(request.form['name'])

        if Wiki.is_registered(wiki_name):
            flash("Site already exists")
            return redirect(redirect_url())
        else:
            s = Site()
            s.create(name=wiki_name, repo=wiki_name, founder=g.current_user.get('id'))
            return redirect('http://%s.%s' % (wiki_name, config.HOSTNAME))
    else:
        return render_template('wiki/new.html')
    
    
@blueprint.route("/wiki/_commit/<sha>/<name>")
def commit(name, sha):
    cname = to_canonical(name)

    data = g.current_wiki.get_page(cname, sha=sha)
    if data:
        return render_template('wiki/page.html', name=name, page=data, commit=sha)
    else:
        return redirect(url_for('.create', name=cname))


@blueprint.route("/wiki/_compare/<name>/<regex('[^.]+'):fsha><regex('\.{2,3}'):dots><regex('.+'):lsha>")
def compare(name, fsha, dots, lsha):
    diff = g.current_wiki.compare(name, fsha, lsha)
    return render_template('wiki/compare.html', name=name, diff=diff, old=fsha, new=lsha)


@blueprint.route("/wiki/_revert", methods=['POST'])
def revert():
    if request.method == 'POST':
        name = request.form.get('name')
        commit = request.form.get('commit')
        cname = to_canonical(name)
        g.current_wiki.revert_page(name, commit, message="Reverting %s" % cname, username=g.current_user.get('username'))
        flash('Page reverted', 'success')
        return redirect(url_for('.page', name=cname))
    
@blueprint.route("/wiki/_history/<name>")
def history(name):
    history = g.current_wiki.get_history(name)
    return render_template('wiki/history.html', name=name, history=history, wiki_home=url_for('wiki.page'))


@blueprint.route("/wiki/_edit/<name>", methods=['GET', 'POST'])
def edit(name):
    data = g.current_wiki.get_page(name)
    cname = to_canonical(name)
    if request.method == 'POST':
        edit_cname = to_canonical(request.form['name'])
        if edit_cname.lower() != cname.lower():
            g.current_wiki.rename_page(cname, edit_cname)
        g.current_wiki.write_page(edit_cname,
                                  request.form['content'],
                                  message=request.form['message'],
                                  username=g.current_user.get('username'))
        return redirect(url_for('.page', name=edit_cname))
    else:
        if data:
            name = remove_ext(data['name'])
            content = data['data']
            return render_template('wiki/edit.html', name=name, content=content)
        else:
            return redirect(url_for('.create', name=cname))


@blueprint.route("/wiki/_delete/<name>", methods=['POST'])
@login_required
def delete(name):
    pass


@blueprint.route("/wiki/_create/", defaults={'name': None}, methods=['GET', 'POST'])
@blueprint.route("/wiki/_create/<name>", methods=['GET', 'POST'])
def create(name):
    cname = ""
    if name:
        cname = to_canonical(name)
        if g.current_wiki.get_page(cname):
            # Page exists, edit instead
            return redirect(url_for('.edit', name=cname))

    if request.method == 'POST':
        g.current_wiki.write_page(request.form['name'],
                                  request.form['content'],
                                  message=request.form['message'],
                                  create=True,
                                  username=g.current_user.get('username'))
        return redirect(url_for('.page', name=cname))
    else:
        return render_template('wiki/edit.html', name=cname, content="")


@blueprint.route("/wiki/", defaults={'name': 'home'})
@blueprint.route("/wiki/<name>")
def page(name):
    cname = to_canonical(name)
    if cname != name:
        return redirect(url_for('.page', name=cname))

    data = g.current_wiki.get_page(cname)

    if data:
        return render_template('wiki/page.html', name=cname, page=data)
    else:
        return redirect(url_for('.create', name=cname))