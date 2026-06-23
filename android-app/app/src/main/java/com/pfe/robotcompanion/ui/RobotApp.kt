package com.pfe.robotcompanion.ui

import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.pfe.robotcompanion.RobotViewModel
import com.pfe.robotcompanion.data.ConnectionState
import com.pfe.robotcompanion.data.ConversationEntry
import com.pfe.robotcompanion.data.RobotUiState
import java.util.Locale
import kotlin.math.roundToInt

private data class AppTab(val label: String, val icon: ImageVector)

private val appTabs = listOf(
    AppTab("Map", Icons.Default.Map),
    AppTab("Conversation", Icons.AutoMirrored.Filled.Chat),
    AppTab("Control", Icons.Default.Tune),
)

@Composable
fun RobotApp(
    viewModel: RobotViewModel,
    onMicrophoneClick: () -> Unit,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val connected = state.connection == ConnectionState.CONNECTED
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { RobotTopBar(state, viewModel::disconnect, viewModel::emergencyStop) },
        bottomBar = {
            if (connected) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    appTabs.forEachIndexed { index, tab ->
                        NavigationBarItem(
                            selected = selectedTab == index,
                            onClick = { selectedTab = index },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(tab.label) },
                        )
                    }
                }
            }
        },
    ) { innerPadding ->
        if (connected) {
            when (selectedTab) {
                0 -> MapScreen(state, viewModel, Modifier.padding(innerPadding))
                1 -> ConversationScreen(state, viewModel, onMicrophoneClick, Modifier.padding(innerPadding))
                else -> ControlScreen(state, viewModel, Modifier.padding(innerPadding))
            }
        } else {
            ConnectionScreen(state, viewModel, Modifier.padding(innerPadding))
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun RobotTopBar(state: RobotUiState, disconnect: () -> Unit, emergencyStop: () -> Unit) {
    val connected = state.connection == ConnectionState.CONNECTED
    TopAppBar(
        colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface),
        title = {
            Column {
                Text(
                    if (connected) state.robotName else "Robot Companion",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                )
                Text(
                    state.connectionMessage,
                    style = MaterialTheme.typography.labelMedium,
                    color = if (connected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        },
        navigationIcon = {
            Surface(
                shape = CircleShape,
                color = if (connected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
                modifier = Modifier.padding(start = 16.dp, end = 10.dp),
            ) {
                Icon(
                    if (connected) Icons.Default.Wifi else Icons.Default.WifiOff,
                    contentDescription = null,
                    tint = if (connected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(9.dp).size(20.dp),
                )
            }
        },
        actions = {
            if (connected) {
                Button(
                    onClick = emergencyStop,
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                    contentPadding = PaddingValues(horizontal = 12.dp),
                    modifier = Modifier.height(40.dp),
                ) {
                    Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("STOP", fontWeight = FontWeight.Bold)
                }
                IconButton(onClick = disconnect) {
                    Icon(Icons.Default.WifiOff, contentDescription = "Disconnect")
                }
            }
        },
    )
}

@Composable
private fun ConnectionScreen(state: RobotUiState, viewModel: RobotViewModel, modifier: Modifier = Modifier) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            ElevatedCard(
                colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                shape = RoundedCornerShape(28.dp),
            ) {
                Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(Icons.Default.Wifi, null, Modifier.size(34.dp), MaterialTheme.colorScheme.primary)
                    Text("Connect to your robot", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    Text(
                        "Use the robot address on the same trusted Wi-Fi network.",
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                    )
                }
            }
        }
        item {
            ElevatedCard(shape = RoundedCornerShape(24.dp)) {
                Column(
                    Modifier.fillMaxWidth().padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    SectionTitle("Connection", "Secure local pairing", Icons.Default.Settings)
                    OutlinedTextField(
                        value = state.endpoint.host,
                        onValueChange = { viewModel.updateEndpoint(host = it) },
                        label = { Text("Robot IP address") },
                        placeholder = { Text("192.168.1.25") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(
                            value = state.endpoint.port.toString(),
                            onValueChange = { viewModel.updateEndpoint(port = it) },
                            label = { Text("Port") },
                            singleLine = true,
                            modifier = Modifier.weight(0.38f),
                        )
                        OutlinedTextField(
                            value = state.endpoint.token,
                            onValueChange = { viewModel.updateEndpoint(token = it) },
                            label = { Text("Pairing token") },
                            visualTransformation = PasswordVisualTransformation(),
                            singleLine = true,
                            modifier = Modifier.weight(0.62f),
                        )
                    }
                    Button(
                        onClick = viewModel::connect,
                        enabled = state.connection != ConnectionState.CONNECTING,
                        modifier = Modifier.fillMaxWidth().height(54.dp),
                        shape = RoundedCornerShape(16.dp),
                    ) {
                        Icon(Icons.Default.Wifi, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text(if (state.connection == ConnectionState.CONNECTING) "Connecting..." else "Connect")
                    }
                }
            }
        }
    }
}

@Composable
private fun MapScreen(state: RobotUiState, viewModel: RobotViewModel, modifier: Modifier = Modifier) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, top = 14.dp, end = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { SectionHeader("Robot map", "Live position and navigation status") }
        item { MapCard(state, viewModel) }
        item { RobotStatusCard(state) }
    }
}

@Composable
private fun ConversationScreen(
    state: RobotUiState,
    viewModel: RobotViewModel,
    onMicrophoneClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    LaunchedEffect(state.conversation.size) {
        if (state.conversation.isNotEmpty()) {
            listState.animateScrollToItem(state.conversation.lastIndex)
        }
    }

    Column(modifier.fillMaxSize()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            SectionHeader("Robot conversation", "Speak or type a command")
        }
        if (state.conversation.isEmpty()) {
            Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = null, modifier = Modifier.size(42.dp), tint = MaterialTheme.colorScheme.outline)
                    Spacer(Modifier.height(8.dp))
                    Text("Start a conversation", fontWeight = FontWeight.SemiBold)
                    Text("Use the microphone or message field below.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(
                    items = state.conversation,
                    key = { "${it.id}-${it.role}-${it.timestamp}" },
                ) { entry ->
                    ChatBubble(entry)
                }
            }
        }
        ConversationComposer(state, viewModel, onMicrophoneClick)
    }
}

@Composable
private fun ControlScreen(state: RobotUiState, viewModel: RobotViewModel, modifier: Modifier = Modifier) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, top = 14.dp, end = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { SectionHeader("Parameters & control", "Drive speed, manual movement and voice settings") }
        item { ManualControlCard(state, viewModel) }
        item { ParametersCard(state, viewModel) }
        item { SafetyCard(viewModel) }
    }
}

@Composable
private fun MapCard(state: RobotUiState, viewModel: RobotViewModel) {
    ElevatedCard(shape = RoundedCornerShape(24.dp)) {
        Column {
            Row(
                Modifier.fillMaxWidth().padding(start = 18.dp, top = 14.dp, end = 8.dp, bottom = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(state.map?.name ?: "Robot map", fontWeight = FontWeight.SemiBold)
                    Text(
                        when {
                            !state.pose.localized -> "Waiting for robot position"
                            state.pose.poseAge > 5.0 -> "Showing last known position | pinch and drag"
                            else -> "Live position | pinch to zoom and drag"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                StatusPill(state.status.mode)
                IconButton(onClick = viewModel::requestMap) {
                    Icon(Icons.Default.Refresh, contentDescription = "Reload map")
                }
            }
            RobotMap(
                map = state.map,
                pose = state.pose,
                status = state.status,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(280.dp)
                    .padding(horizontal = 10.dp)
                    .clip(RoundedCornerShape(18.dp)),
            )
            Row(
                Modifier.fillMaxWidth().padding(14.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Metric("X", formatDecimal(state.pose.x), Modifier.weight(1f))
                Metric("Y", formatDecimal(state.pose.y), Modifier.weight(1f))
                Metric("Heading", "${Math.toDegrees(state.pose.yaw).roundToInt()}°", Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun ChatBubble(entry: ConversationEntry) {
    val isUser = entry.role == "user"
    val isRobot = entry.role == "robot"
    Box(Modifier.fillMaxWidth()) {
        Surface(
            modifier = Modifier
                .fillMaxWidth(0.84f)
                .align(if (isUser) Alignment.CenterEnd else Alignment.CenterStart),
            shape = RoundedCornerShape(
                topStart = 20.dp,
                topEnd = 20.dp,
                bottomStart = if (isUser) 20.dp else 5.dp,
                bottomEnd = if (isUser) 5.dp else 20.dp,
            ),
            color = when {
                isUser -> MaterialTheme.colorScheme.primary
                isRobot -> MaterialTheme.colorScheme.primaryContainer
                else -> MaterialTheme.colorScheme.errorContainer
            },
            contentColor = when {
                isUser -> MaterialTheme.colorScheme.onPrimary
                isRobot -> MaterialTheme.colorScheme.onPrimaryContainer
                else -> MaterialTheme.colorScheme.onErrorContainer
            },
        ) {
            Column(Modifier.padding(horizontal = 15.dp, vertical = 11.dp)) {
                Text(
                    when (entry.role) { "user" -> "You"; "robot" -> "Robot"; else -> "System" },
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(entry.text, style = MaterialTheme.typography.bodyMedium)
                Text(
                    entry.state.replace('_', ' '),
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.align(Alignment.End),
                )
            }
        }
    }
}

@Composable
private fun ManualControlCard(state: RobotUiState, viewModel: RobotViewModel) {
    ElevatedCard(shape = RoundedCornerShape(24.dp)) {
        Column(
            Modifier.fillMaxWidth().padding(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(Modifier.fillMaxWidth()) {
                SectionTitle("Manual drive", "Press and hold; release stops", Icons.Default.Tune)
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Drive speed", fontWeight = FontWeight.SemiBold)
                Text(
                    "${(state.endpoint.teleopSpeed * 100).roundToInt()}%",
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold,
                )
            }
            Slider(
                value = state.endpoint.teleopSpeed,
                onValueChange = viewModel::updateTeleopSpeed,
                onValueChangeFinished = viewModel::saveTeleopSpeed,
                valueRange = 0.2f..1.0f,
                steps = 7,
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                "20% precision control to 100% maximum configured speed",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.fillMaxWidth(),
            )
            DriveButton("forward", "Forward", Icons.Default.KeyboardArrowUp, state, viewModel, Modifier.fillMaxWidth(0.58f))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                DriveButton("left", "Turn left", Icons.AutoMirrored.Filled.KeyboardArrowLeft, state, viewModel, Modifier.weight(1f))
                FilledIconButton(
                    onClick = { viewModel.stopTeleop() },
                    modifier = Modifier.size(58.dp),
                    colors = androidx.compose.material3.IconButtonDefaults.filledIconButtonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                    ),
                ) {
                    Icon(Icons.Default.Stop, contentDescription = "Stop manual movement")
                }
                DriveButton("right", "Turn right", Icons.AutoMirrored.Filled.KeyboardArrowRight, state, viewModel, Modifier.weight(1f))
            }
            DriveButton("backward", "Reverse", Icons.Default.KeyboardArrowDown, state, viewModel, Modifier.fillMaxWidth(0.58f))
            Text(
                state.teleopDirection?.let { "Manual movement: ${driveLabel(it)}" } ?: "Dead-man control ready",
                style = MaterialTheme.typography.labelMedium,
                color = if (state.teleopDirection != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun DriveButton(
    direction: String,
    label: String,
    icon: ImageVector,
    state: RobotUiState,
    viewModel: RobotViewModel,
    modifier: Modifier = Modifier,
) {
    val active = state.teleopDirection == direction
    Surface(
        modifier = modifier
            .height(66.dp)
            .semantics {
                role = Role.Button
                contentDescription = "$label. Press and hold to move."
            }
            .pointerInput(direction) {
                detectTapGestures(
                    onPress = {
                        viewModel.startTeleop(direction)
                        try {
                            tryAwaitRelease()
                        } finally {
                            viewModel.stopTeleop(direction)
                        }
                    },
                )
            },
        shape = RoundedCornerShape(18.dp),
        color = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.primaryContainer,
        contentColor = if (active) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onPrimaryContainer,
        shadowElevation = if (active) 5.dp else 0.dp,
    ) {
        Row(
            Modifier.fillMaxSize().padding(horizontal = 10.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(26.dp))
            Spacer(Modifier.width(4.dp))
            Text(label, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun ParametersCard(state: RobotUiState, viewModel: RobotViewModel) {
    ElevatedCard(shape = RoundedCornerShape(24.dp)) {
        Column(
            Modifier.fillMaxWidth().padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SectionTitle("Parameters", "Voice recognition preferences", Icons.Default.Settings)
            SettingRow(
                title = "Auto-send final speech",
                subtitle = "Send when recognition finishes",
                checked = state.endpoint.autoSendSpeech,
                onCheckedChange = viewModel::updateAutoSendSpeech,
            )
            HorizontalDivider()
            SettingRow(
                title = "Force offline recognition",
                subtitle = "Use Android offline or embedded English",
                checked = state.endpoint.offlineSpeech,
                onCheckedChange = viewModel::updateOfflineSpeech,
            )
            HorizontalDivider()
            SettingRow(
                title = "Robot physical microphone",
                subtitle = if (state.robotMicEnabled) {
                    "Robot mic is listening until you turn it off"
                } else {
                    "Muted on the Jetson; use this only when needed"
                },
                checked = state.robotMicEnabled,
                onCheckedChange = viewModel::setRobotMicEnabled,
            )
            OutlinedTextField(
                value = state.endpoint.locale,
                onValueChange = viewModel::updateLocale,
                label = { Text("Speech locale") },
                supportingText = { Text("Embedded fallback supports en-US") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun SafetyCard(viewModel: RobotViewModel) {
    ElevatedCard(
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Safety controls", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text("These actions interrupt the current task immediately.", color = MaterialTheme.colorScheme.onErrorContainer)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = viewModel::cancelSearch, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Default.Cancel, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("Cancel search")
                }
                Button(
                    onClick = viewModel::emergencyStop,
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(Icons.Default.Stop, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("STOP ALL")
                }
            }
        }
    }
}

@Composable
private fun ConversationComposer(
    state: RobotUiState,
    viewModel: RobotViewModel,
    onMicrophoneClick: () -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 12.dp,
        modifier = Modifier.imePadding(),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                FilledIconButton(
                    onClick = if (state.isListening) viewModel::stopSpeech else onMicrophoneClick,
                    modifier = Modifier.size(50.dp),
                    colors = androidx.compose.material3.IconButtonDefaults.filledIconButtonColors(
                        containerColor = if (state.isListening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                    ),
                ) {
                    Icon(
                        if (state.isListening) Icons.Default.MicOff else Icons.Default.Mic,
                        contentDescription = if (state.isListening) "Stop voice recording" else "Start voice recording",
                    )
                }
                OutlinedTextField(
                    value = state.transcript,
                    onValueChange = viewModel::setTranscript,
                    placeholder = { Text(if (state.isListening) "Listening..." else "Message the robot") },
                    singleLine = true,
                    shape = RoundedCornerShape(18.dp),
                    modifier = Modifier.weight(1f),
                )
                FilledTonalIconButton(
                    onClick = viewModel::sendTranscript,
                    enabled = state.transcript.isNotBlank(),
                    modifier = Modifier.size(50.dp),
                ) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send command")
                }
            }
            Text(
                if (state.isListening) "Recording voice" else state.speechMode,
                style = MaterialTheme.typography.labelSmall,
                color = if (state.isListening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(horizontal = 58.dp),
            )
        }
    }
}

@Composable
private fun RobotStatusCard(state: RobotUiState) {
    ElevatedCard(shape = RoundedCornerShape(24.dp)) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SectionTitle("Current task", state.status.phase.replace('_', ' '), Icons.Default.CheckCircle)
            Text(state.status.message.ifBlank { "Robot is ready for a command." })
            if (state.status.waypointTotal > 0) {
                Text(
                    "Waypoint ${state.status.waypointIndex} of ${state.status.waypointTotal}",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, subtitle: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun SectionTitle(title: String, subtitle: String, icon: ImageVector) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.primaryContainer) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(8.dp).size(20.dp))
        }
        Column {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun StatusPill(mode: String) {
    val active = mode in setOf("ready", "searching", "manual", "found")
    Surface(
        shape = RoundedCornerShape(50),
        color = if (active) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.errorContainer,
    ) {
        Text(
            mode.ifBlank { "ready" }.replace('_', ' ').uppercase(Locale.ROOT),
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
        )
    }
}

@Composable
private fun Metric(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(modifier, shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
        Column(Modifier.padding(10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(value, fontWeight = FontWeight.Bold)
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun SettingRow(title: String, subtitle: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

private fun driveLabel(direction: String): String = when (direction) {
    "left" -> "turn left"
    "right" -> "turn right"
    "backward" -> "reverse"
    else -> direction
}

private fun formatDecimal(value: Double): String = String.format(Locale.US, "%.2f", value)
