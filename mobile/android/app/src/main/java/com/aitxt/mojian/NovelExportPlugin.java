package com.aitxt.mojian;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONObject;

/** Stream an authenticated export to cache, then let Android choose its destination. */
@CapacitorPlugin(name = "NovelExport")
public class NovelExportPlugin extends Plugin {
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile File pendingFile;
    private volatile PluginCall pendingCall;
    private volatile HttpURLConnection connection;
    private volatile boolean destroyed;
    private String pendingName;

    @PluginMethod
    public synchronized void saveNovel(PluginCall call) {
        if (pendingCall != null) {
            call.reject("已有导出正在保存，请先完成或取消。", "BUSY");
            return;
        }
        String token = call.getString("token", "");
        // Do not retain the bearer token in a saved activity's PluginCall options.
        call.getData().remove("token");
        String format = call.getString("format", "txt");
        URI uri;
        try {
            uri = new URI(call.getString("url", ""));
            boolean secure = "https".equals(uri.getScheme()) && uri.getHost() != null;
            boolean server = "http".equals(uri.getScheme()) && "49.232.132.111".equals(uri.getHost());
            if ((!secure && !server) || uri.getUserInfo() != null || uri.getFragment() != null ||
                !uri.getPath().matches("/api/v1/projects/[1-9][0-9]*/export") ||
                !("txt".equals(format) || "md".equals(format))) {
                throw new IllegalArgumentException();
            }
        } catch (Exception error) {
            call.reject("小说导出地址或格式无效。", "INVALID");
            return;
        }
        if (!token.matches("[A-Za-z0-9_-]{20,200}")) {
            call.reject("请先登录后再导出小说。", "401");
            return;
        }
        pendingCall = call;
        JSObject data = call.getObject("data");
        io.execute(() -> download(call, uri, token, data));
    }

    private void download(PluginCall call, URI uri, String token, JSObject data) {
        try {
            connection = (HttpURLConnection) uri.toURL().openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(60000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Accept", "application/zip");
            connection.setRequestProperty("Cache-Control", "no-store");
            if (data != null) {
                byte[] body = data.toString().getBytes(StandardCharsets.UTF_8);
                connection.setRequestMethod("POST");
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream request = connection.getOutputStream()) {
                    request.write(body);
                }
            }
            int status = connection.getResponseCode();
            if (status != 200) {
                String message = "导出失败（" + status + "），请确认服务器已更新。";
                try (InputStream error = connection.getErrorStream()) {
                    if (error != null) {
                        byte[] bytes = new byte[8192];
                        int size = error.read(bytes);
                        if (size > 0) {
                            Object detail = new JSONObject(new String(bytes, 0, size, StandardCharsets.UTF_8)).opt("detail");
                            if (detail instanceof String) message = (String) detail;
                        }
                    }
                } catch (Exception ignored) { /* Keep the HTTP status message. */ }
                fail(call, message, Integer.toString(status));
                return;
            }
            pendingName = "novel.zip";
            String disposition = connection.getHeaderField("Content-Disposition");
            if (disposition != null) {
                Matcher match = Pattern.compile("filename\\*=UTF-8''([^;]+)", Pattern.CASE_INSENSITIVE).matcher(disposition);
                if (match.find()) pendingName = URLDecoder.decode(match.group(1), "UTF-8");
            }
            pendingName = pendingName.replaceAll("[\\x00-\\x1f\\x7f<>:\"/\\\\|?*]", "_");
            pendingFile = File.createTempFile("novel-export-", ".zip", getContext().getCacheDir());
            try (InputStream input = connection.getInputStream(); OutputStream output = new FileOutputStream(pendingFile)) {
                copy(input, output);
            }
            if (destroyed) { cleanup(); return; }
            getActivity().runOnUiThread(() -> {
                if (destroyed) return;
                Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("application/zip");
                intent.putExtra(Intent.EXTRA_TITLE, pendingName);
                try {
                    startActivityForResult(call, intent, "saveDocument");
                } catch (Exception error) {
                    fail(call, "无法打开系统文件选择器，请确认手机已安装文件管理器。", "PICKER");
                }
            });
        } catch (Exception error) {
            if (!destroyed) fail(call, "下载小说失败，请检查网络或手机剩余空间后重试。", "DOWNLOAD");
        } finally {
            if (connection != null) connection.disconnect();
            connection = null;
            if (destroyed) cleanup();
        }
    }

    @ActivityCallback
    protected void saveDocument(PluginCall call, ActivityResult result) {
        if (call == null) { cleanup(); return; }
        if (result.getResultCode() != Activity.RESULT_OK) {
            cleanup();
            call.resolve(new JSObject().put("cancelled", true));
            return;
        }
        Uri target = result.getData() == null ? null : result.getData().getData();
        File source = pendingFile;
        String name = pendingName;
        if (target == null || source == null) {
            fail(call, "导出已中断，请重新导出小说。", "INTERRUPTED");
            return;
        }
        io.execute(() -> {
            try (InputStream input = new FileInputStream(source);
                 OutputStream output = getContext().getContentResolver().openOutputStream(target, "wt")) {
                if (output == null) throw new java.io.IOException();
                copy(input, output);
                output.flush();
            } catch (Exception error) {
                fail(call, "保存失败，请重新导出并选择可写入的位置。", "SAVE");
                return;
            }
            cleanup();
            call.resolve(new JSObject().put("saved", true).put("filename", name));
        });
    }

    private void copy(InputStream input, OutputStream output) throws java.io.IOException {
        byte[] buffer = new byte[64 * 1024];
        int size;
        while ((size = input.read(buffer)) != -1) {
            if (destroyed || Thread.currentThread().isInterrupted()) throw new java.io.IOException();
            output.write(buffer, 0, size);
        }
    }

    private synchronized void cleanup() {
        if (pendingFile != null) pendingFile.delete();
        pendingFile = null;
        pendingCall = null;
    }

    private void fail(PluginCall call, String message, String code) {
        cleanup();
        call.reject(message, code);
    }

    @Override
    protected void handleOnDestroy() {
        destroyed = true;
        if (connection != null) connection.disconnect();
        io.shutdownNow();
        cleanup();
    }
}
