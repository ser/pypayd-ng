""" Main API interface """
import threading
import json
import logging
import flask
from flask.ext.httpauth import HTTPBasicAuth
from cherrypy import wsgiserver
import jsonrpc
from jsonrpc import dispatcher
from . import config, qr

class API:
    """ API object """
    def __init__(self):
        self.server = None

    def serve_forever(self, payment_handler, threaded=True):
        if threaded:
            self.serving = threading.Thread(target=self._run, args=(payment_handler,), daemon=True)
            self.serving.start()
            return self.serving.is_alive(), self.serving.ident
        else:
            self._run(payment_handler)

    def _run(self, payment_handler):
        app = flask.Flask(__name__)
        auth = HTTPBasicAuth()

        @dispatcher.add_method
        def is_ready():
            try:
                payment_handler.checkBlockchainService()
                payment_handler.checkPriceInfo()
            except: return False
            return True

        @dispatcher.add_method
        def create_order(amount, currency=config.DEFAULT_CURRENCY,
                         item_number=None, order_id=None, qr_code=False):
            """ calling create_order from payments module """
            ret = payment_handler.create_order(amount, currency, item_number, order_id)
            if ret.get('error'):
                return ret
            ret.update({'qr_image': (qr.bitcoinqr(ret['receiving_address']) if qr_code else None)})
            return ret

        # @dispatcher.add_method
        # def check_order_status(order_id=None, special_digits=None, timestamp=None, payment_address=None):
        #     return payment_handler.CheckPaymentsFor(order_id=order_id, special_digits=special_digits, payment_address=payment_address, timestamp=timestamp)

        @dispatcher.add_method
        def get_payments(bindings={}):
            return payment_handler.db.getPayments(bindings)

        @dispatcher.add_method
        def poll_payments(bindings={}):
            return payment_handler.pollPayments(bindings)

        @dispatcher.add_method
        def get_orders(bindings={}):
            return payment_handler.db.getOrders(bindings)

        @dispatcher.add_method
        def get_address(bindings={}):
            return payment_handler.db.getAddresses(bindings)

        @dispatcher.add_method
        def get_unfilled_orders(timestamp=None):
            statement = "select * from orders where filled = 0 and created_at > ?"
            return payment_handler.db.rquery(statement, (timestamp or 0, ))

        @dispatcher.add_method
        def get_filled_orders(timestamp=None):
            statement = "select * from orders where filled != 0 and created_at > ?"
            return payment_handler.db.rquery(statement, (timestamp or 0, ))

        @dispatcher.add_method
        def query(statement, bindings=()):
            return payment_handler.db.rquery(statement, bindings)

        @auth.get_password
        def get_pw(username):
            if username == config.RPC_USER:
                return config.RPC_PASSWORD
            return None

        @app.route('/', methods=["POST",])
        @app.route('/api', methods=["POST",])
        def handle_post():
            """ handle_post """
            # Dispatcher is a dictionary {<method_name>: callable}
            try:
                request_json = flask.request.get_data().decode('utf-8')
                request_data = json.loads(request_json)
                assert('id' in request_data and request_data['jsonrpc'] == "2.0"
                       and request_data['method'])
            except:
                obj_error = jsonrpc.exceptions.JSONRPCInvalidRequest(
                    data="Invalid JSON-RPC 2.0 request format")
                return flask.Response(obj_error.json.encode(), 200, mimetype='application/json')
            jsonrpc_response = jsonrpc.JSONRPCResponseManager.handle(request_json, dispatcher)
            response = flask.Response(
                jsonrpc_response.json.encode(), 200, mimetype='application/json')
            return response

        if config.AUTH_REQUIRED:
            auth.login_required(handle_post)
        daemon = wsgiserver.WSGIPathInfoDispatcher({'/': app.wsgi_app})
        self.server = wsgiserver.CherryPyWSGIServer((config.RPC_HOST, config.RPC_PORT), daemon)
        logging.info("API Started on %s", (config.RPC_HOST + ':' + str(config.RPC_PORT)))
        self.server.start()

