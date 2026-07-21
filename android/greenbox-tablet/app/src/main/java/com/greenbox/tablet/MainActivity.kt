package com.greenbox.tablet

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.greenbox.tablet.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var backendBaseUrl: String = BuildConfig.DEFAULT_BACKEND_URL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        backendBaseUrl = Prefs.backendUrl(this)
        applyKeepScreenOn()
        setupWebView()
        setupActions()
        loadDashboard()
    }

    override fun onResume() {
        super.onResume()
        val base = Prefs.backendUrl(this)
        applyKeepScreenOn()
        if (base != backendBaseUrl) {
            backendBaseUrl = base
            loadDashboard()
        }
    }

    private fun setPullRefreshEnabled(enabled: Boolean) {
        binding.swipeRefresh.isEnabled = enabled
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        binding.webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            cacheMode = WebSettings.LOAD_DEFAULT
            useWideViewPort = true
            loadWithOverviewMode = true
            builtInZoomControls = false
            displayZoomControls = false
            mediaPlaybackRequiresUserGesture = false
            userAgentString = "${userAgentString} GreenboxTablet/1.0"
        }

        binding.webView.addJavascriptInterface(AndroidBridge(), "GreenboxAndroid")

        binding.webView.webChromeClient = WebChromeClient()
        binding.webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                binding.swipeRefresh.isRefreshing = false
                binding.offlinePanel.visibility = View.GONE
                binding.webView.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                binding.swipeRefresh.isRefreshing = false
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    showOffline()
                }
            }
        }

        binding.swipeRefresh.setColorSchemeResources(R.color.brand)
        binding.swipeRefresh.setOnRefreshListener { loadDashboard() }
    }

    private fun setupActions() {
        binding.retryButton.setOnClickListener { loadDashboard() }
        binding.configureButton.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
    }

    fun loadDashboard() {
        backendBaseUrl = Prefs.backendUrl(this)
        binding.swipeRefresh.isRefreshing = true
        binding.webView.loadUrl(dashboardUrl())
    }

    private fun dashboardUrl(): String {
        val base = backendBaseUrl.trim().trimEnd('/')
        return "$base/?app=tablet"
    }

    private fun showOffline() {
        binding.swipeRefresh.isRefreshing = false
        binding.webView.visibility = View.GONE
        binding.offlinePanel.visibility = View.VISIBLE
    }

    private fun applyKeepScreenOn() {
        if (Prefs.keepScreenOn(this)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }

        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, binding.root).let { controller ->
            controller.hide(WindowInsetsCompat.Type.statusBars())
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (binding.webView.canGoBack()) {
            binding.webView.goBack()
        } else {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    private inner class AndroidBridge {
        @JavascriptInterface
        fun setPullRefreshEnabled(enabled: Boolean) {
            runOnUiThread { this@MainActivity.setPullRefreshEnabled(enabled) }
        }

        @JavascriptInterface
        fun reload() {
            runOnUiThread { loadDashboard() }
        }

        @JavascriptInterface
        fun openSettings() {
            runOnUiThread {
                startActivity(Intent(this@MainActivity, SettingsActivity::class.java))
            }
        }
    }
}
