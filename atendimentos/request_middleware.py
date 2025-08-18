import threading

_request_storage = threading.local()

def get_current_request():
    return getattr(_request_storage, 'request', None)

def get_current_user():
    request = get_current_request()
    if request:
        return request.user
    return None

class RequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _request_storage.request = request
        response = self.get_response(request)
        return response
