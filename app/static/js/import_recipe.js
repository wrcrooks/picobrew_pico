$(document).ready(function(){
	$('.import-recipe-form button').click(function(){
        var form = $(this).closest('form')[0];
        var import_data = {}

        var rfid_element = form.elements['rfid']
        if (rfid_element != undefined)
            import_data.rfid = rfid_element.value;

        var uid_element = form.elements['uid']
        if (uid_element != undefined)
            import_data.uid = uid_element.value;

        var guid_element = form.elements['guid']
        if (guid_element != undefined)
            import_data.guid = guid_element.value;

        var postUrl = form.dataset.postUrl;
        var redirectUrl = form.dataset.redirect;

		$.ajax({
			url: postUrl,
			type: 'POST',
            data: JSON.stringify(import_data),
            dataType: "json",
            processData: false,
            contentType: "application/json; charset=UTF-8",
            success: function(data) {
                showAlert("Success!", "success");
                setTimeout(function () {
                    window.location.href = redirectUrl;
                }, 2000);
            },
            error: function(request, status, error) {
                showAlert("Error: " + request.responseText, "danger");
            },
		});
    });
    function showAlert(msg, type){
        $('#alert').html("<div class='w-50 alert text-center alert-" + type + "'>" + msg + "</div>");
        $('#alert').show();
    }
});
