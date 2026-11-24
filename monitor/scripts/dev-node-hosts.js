"use strict";

// Только для запуска мониторинга без Docker: hostname MeshCentral из compose
// (`meshcentral`) заменяется на адрес, доступный с хоста. Node не читает
// /etc/hosts через dns.lookup, поэтому подменяем аргументы tls/net на входе.
// В контейнере этот файл не используется.

const overrides = (() => {
  try {
    return JSON.parse(process.env.MON_DEV_DNS_OVERRIDES || "{}");
  } catch (_) {
    return {};
  }
})();

function mapped(host) {
  if (typeof host !== "string") return null;
  return Object.prototype.hasOwnProperty.call(overrides, host) ? overrides[host] : null;
}

const tls = require("tls");
const originalTlsConnect = tls.connect;
tls.connect = function (options, ...rest) {
  if (options && typeof options === "object") {
    const ip = mapped(options.host);
    if (ip) {
      options = Object.assign({}, options, {
        host: ip,
        servername: options.servername || options.host,
      });
    }
  }
  return originalTlsConnect.call(this, options, ...rest);
};

const net = require("net");
const originalNetConnect = net.connect;
net.connect = function (options, ...rest) {
  if (typeof options === "string") {
    const ip = mapped(options);
    if (ip) options = ip;
  } else if (options && typeof options === "object") {
    const ip = mapped(options.host);
    if (ip) options = Object.assign({}, options, { host: ip });
  }
  return originalNetConnect.call(this, options, ...rest);
};
net.createConnection = net.connect;
