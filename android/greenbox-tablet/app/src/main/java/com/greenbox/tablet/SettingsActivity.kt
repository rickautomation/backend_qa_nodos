package com.greenbox.tablet

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.greenbox.tablet.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = getString(R.string.settings_title)

        binding.backendUrlInput.setText(Prefs.backendUrl(this))
        binding.keepScreenOnSwitch.isChecked = Prefs.keepScreenOn(this)

        binding.saveButton.setOnClickListener {
            val raw = binding.backendUrlInput.text?.toString().orEmpty()
            try {
                Prefs.saveBackendUrl(this, raw)
                Prefs.setKeepScreenOn(this, binding.keepScreenOnSwitch.isChecked)
                setResult(RESULT_OK)
                finish()
            } catch (_: IllegalArgumentException) {
                Toast.makeText(this, R.string.invalid_url, Toast.LENGTH_LONG).show()
            }
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}
