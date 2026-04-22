import argparse
import controller.app as controller_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Path to config file')
    args = parser.parse_args()
    
    controller_app.init_controller(args.config)
    # Access the updated config from the module
    port = controller_app.config.controller.port
    controller_app.app.run(port=port, debug=False, use_reloader=False)
