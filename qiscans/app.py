"""Internal HTTP API compatible with Mangaka's external source adapter."""
from flask import Flask, Response, jsonify, request

from .client import Qiscans, SourceError, checked_url


def create_app(service=None):
    app = Flask(__name__)
    service = service or Qiscans()
    app.extensions["qiscans"] = service

    @app.errorhandler(SourceError)
    def source_error(error):
        response = jsonify(source="qiscans", error=str(error))
        response.status_code = error.status
        if error.retry_after:
            response.headers["Retry-After"] = str(error.retry_after)
        return response

    @app.before_request
    def source():
        if request.args.get("source", "qiscans") != "qiscans":
            raise SourceError("Fonte desconhecida.", 400)

    def payload(data):
        return jsonify(source="qiscans", **data)

    @app.get("/api/health")
    def health():
        return payload({"status": "ok"})

    def listing():
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            raise SourceError("Página inválida.", 400) from None
        if not 1 <= page <= 500:
            raise SourceError("Página fora do intervalo permitido.", 400)
        query = " ".join(request.args.get("q", "").split())
        if len(query) > 120:
            raise SourceError("Busca muito longa.", 400)
        return payload(service.listing(page, query or None, request.args.get("tag") or None))

    app.add_url_rule("/api/manga/catalog", "catalog", listing)
    app.add_url_rule("/api/manga/search", "search", listing)

    @app.get("/api/manga/tags")
    def tags():
        return payload({"tags": service.tags()})

    @app.get("/api/manga/<identifier>")
    def info(identifier):
        return payload(service.info(identifier))

    @app.get("/api/manga/<identifier>/chapters")
    def chapters(identifier):
        return payload(service.chapters(identifier))

    @app.get("/api/manga/<identifier>/chapters/<chapter_id>/pages")
    def pages(identifier, chapter_id):
        return payload(service.pages(identifier, chapter_id))

    @app.get("/api/proxy/image")
    def image():
        url = checked_url(request.args.get("url", ""), image=True)
        body, content_type = service.transport.get(url, image=True)
        return Response(body, content_type=content_type, headers={
            "Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    return app
