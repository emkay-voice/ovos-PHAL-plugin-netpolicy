"""ovos-PHAL-plugin-netpolicy — design-stage stub.

Nothing in here touches the firewall or Tor yet. The class exists so the
entry point and config path can be tested against a real ovos_PHAL_admin.
See docs/DESIGN.md.
"""
from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.phal import AdminPlugin, AdminValidator


class NetPolicyAdminValidator(AdminValidator):
    @staticmethod
    def validate(config=None):
        config = config or {}
        # opt-in only: routing changes must never happen by accident
        return config.get("enabled", False) is True


class NetPolicyAdminPlugin(AdminPlugin):
    validator = NetPolicyAdminValidator

    def __init__(self, bus=None, config=None):
        super().__init__(bus=bus, name="ovos-PHAL-plugin-netpolicy", config=config)
        self._on("ovos.netpolicy.status", self.handle_status)
        self._on("ovos.netpolicy.new_identity", self.handle_new_identity)
        self._on("ovos.netpolicy.reload", self.handle_reload)

    def handle_status(self, message: Message):
        # TODO: report Tor bootstrap state and active nftables rules
        self.bus.emit(message.response({"implemented": False,
                                        "routes": self.config.get("routes", {})}))

    def handle_new_identity(self, message: Message):
        # TODO: NEWNYM via Tor control port (stem)
        self.bus.emit(message.response({"implemented": False}))

    def handle_reload(self, message: Message):
        # TODO: re-read config, resolve cgroups, re-apply nftables table
        self.bus.emit(message.response({"implemented": False}))
