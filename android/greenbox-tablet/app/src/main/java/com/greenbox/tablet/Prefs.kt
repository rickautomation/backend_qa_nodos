package com.greenbox.tablet

import android.content.Context
import android.net.Uri

object Prefs {
    private const val NAME = "greenbox_tablet"
    private const val KEY_BACKEND_URL = "backend_url"
    private const val KEY_KEEP_SCREEN_ON = "keep_screen_on"

    fun backendUrl(context: Context): String {
        val prefs = context.getSharedPreferences(NAME, Context.MODE_PRIVATE)
        return prefs.getString(KEY_BACKEND_URL, BuildConfig.DEFAULT_BACKEND_URL)
            ?: BuildConfig.DEFAULT_BACKEND_URL
    }

    fun saveBackendUrl(context: Context, rawUrl: String): String {
        val normalized = normalizeUrl(rawUrl)
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_BACKEND_URL, normalized)
            .apply()
        return normalized
    }

    fun keepScreenOn(context: Context): Boolean {
        return context.getSharedPreferences(NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_KEEP_SCREEN_ON, true)
    }

    fun setKeepScreenOn(context: Context, enabled: Boolean) {
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_KEEP_SCREEN_ON, enabled)
            .apply()
    }

    fun normalizeUrl(rawUrl: String): String {
        val trimmed = rawUrl.trim()
        require(trimmed.isNotEmpty()) { "empty url" }

        val withScheme = if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
            trimmed
        } else {
            "http://$trimmed"
        }

        val uri = Uri.parse(withScheme)
        val host = uri.host ?: throw IllegalArgumentException("invalid host")
        val port = if (uri.port != -1) uri.port else 3000
        val scheme = uri.scheme ?: "http"
        return "$scheme://$host:$port/"
    }
}
