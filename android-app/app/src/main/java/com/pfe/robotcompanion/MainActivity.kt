package com.pfe.robotcompanion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.content.ContextCompat
import com.pfe.robotcompanion.ui.RobotApp
import com.pfe.robotcompanion.ui.RobotCompanionTheme

class MainActivity : ComponentActivity() {
    private val viewModel: RobotViewModel by viewModels()

    private val microphonePermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            viewModel.startSpeech()
        } else {
            viewModel.onMicrophonePermissionDenied()
        }
    }

    private val localNetworkPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestFutureLocalNetworkPermission()
        setContent {
            RobotCompanionTheme {
                RobotApp(
                    viewModel = viewModel,
                    onMicrophoneClick = ::startSpeechWithPermission,
                )
            }
        }
    }

    override fun onStop() {
        viewModel.stopTeleop()
        viewModel.cancelSpeech()
        super.onStop()
    }

    private fun startSpeechWithPermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            viewModel.startSpeech()
        } else {
            microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun requestFutureLocalNetworkPermission() {
        if (Build.VERSION.SDK_INT < 37) return
        val permission = "android.permission.ACCESS_LOCAL_NETWORK"
        if (ContextCompat.checkSelfPermission(this, permission) != PackageManager.PERMISSION_GRANTED) {
            localNetworkPermission.launch(permission)
        }
    }
}
