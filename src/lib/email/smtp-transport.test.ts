import assert from "node:assert/strict";
import test from "node:test";
import nodemailer from "nodemailer";
import { createSmtpTransport } from "./smtp-transport";

test("password SMTP keeps encryption and authentication settings", () => {
  const transport = createSmtpTransport({
    host: "smtp.example.invalid",
    port: 587,
    encryption: "TLS",
    user: "test-user",
    pass: "test-password",
  });
  try {
    const options = transport.options as Record<string, unknown>;
    assert.equal(options.host, "smtp.example.invalid");
    assert.equal(options.secure, false);
    assert.equal(options.requireTLS, true);
    assert.deepEqual(options.auth, { user: "test-user", pass: "test-password" });
  } finally {
    transport.close();
  }
});

test("OAuth SMTP preserves credentials without connecting to a server", () => {
  const transport = createSmtpTransport({
    host: "smtp.example.invalid",
    port: 465,
    encryption: "SSL",
    user: "test-user",
    authType: "oauth2",
    clientId: "test-client",
    clientSecret: "test-secret",
    refreshToken: "test-refresh",
  });
  try {
    const options = transport.options as Record<string, unknown>;
    assert.equal(options.secure, true);
    assert.deepEqual(options.auth, {
      type: "OAuth2",
      user: "test-user",
      clientId: "test-client",
      clientSecret: "test-secret",
      refreshToken: "test-refresh",
      accessToken: undefined,
      accessUrl: undefined,
    });
  } finally {
    transport.close();
  }
});

test("updated mailer composes a UTF-8 message without network or file access", async () => {
  const transport = nodemailer.createTransport({
    streamTransport: true,
    buffer: true,
    disableFileAccess: true,
    disableUrlAccess: true,
  });
  try {
    const result = await transport.sendMail({
      from: "sender@example.invalid",
      to: "recipient@example.invalid",
      subject: "Учебный курс",
      text: "Материал доступен в MAX.",
      html: "<p>Материал доступен в MAX.</p>",
    });
    assert.deepEqual(result.envelope.to, ["recipient@example.invalid"]);
    assert.match(result.message.toString("utf8"), /multipart\/alternative/);
    assert.match(result.message.toString("utf8"), /charset=utf-8/i);
  } finally {
    transport.close();
  }
});
